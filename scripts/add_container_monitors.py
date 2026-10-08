# /// script
# requires-python = ">=3.10"
# dependencies = ["uptime-kuma-api==1.2.1"]
# ///
"""Bulk-create Uptime Kuma "Docker Container" monitors for running containers.

Run on the Docker host (it reads `docker ps`), from anywhere that can reach Kuma:

    export KUMA_URL=https://uptime.tail1234.ts.net
    uv run scripts/add_container_monitors.py --dry-run
    uv run scripts/add_container_monitors.py

What it does (idempotent: re-running only adds what's missing, never edits
or deletes existing monitors):
  1. Makes sure Kuma has a Docker host pointing at the stack's read-only
     socket proxy (http://socket-proxy:2375), creating one if needed.
  2. Creates one group per Compose project ("Containers: <project>"), with a
     Docker Container monitor for every running container in it. Containers
     not started by Compose go in "Containers: standalone".

Kuma's own container is skipped: it can't alert on itself being down.

Environment: KUMA_URL, KUMA_USER (default admin), KUMA_PASSWORD (prompted if
unset). Requires uv (https://docs.astral.sh/uv/), or install uptime-kuma-api
yourself and run it with python3.
"""

import argparse
import getpass
import json
import os
import subprocess
import sys
import warnings

# uptime-kuma-api 1.2.1 has an invalid escape in a docstring; harmless noise.
warnings.filterwarnings("ignore", category=SyntaxWarning)
from uptime_kuma_api import DockerType, MonitorType, UptimeKumaApi  # noqa: E402

SOCKET_PROXY = "http://socket-proxy:2375"
KUMA_IMAGE = "louislam/uptime-kuma"


def v2_compat(api):
    """Let uptime-kuma-api 1.2.1 (written for Kuma 1.x) add monitors on Kuma 2.

    Kuma 2 rejects new monitors without a `conditions` column value, which the
    library never sends. Add an empty list to every "add" call; Kuma 1.x
    ignores the extra field.
    """
    call = api._call

    def _call(event, data=None):
        if event == "add" and isinstance(data, dict):
            data.setdefault("conditions", "[]")
        return call(event, data)

    api._call = _call


def running_containers(skip):
    """{compose project (or 'standalone'): [container names]} from docker ps."""
    out = subprocess.run(
        ["docker", "ps", "--format", "{{json .}}"],
        check=True, capture_output=True, text=True,
    ).stdout
    projects = {}
    for line in out.splitlines():
        c = json.loads(line)
        if c["Image"].split(":")[0] == KUMA_IMAGE or c["Names"] in skip:
            continue
        labels = dict(
            kv.split("=", 1) for kv in c["Labels"].split(",") if "=" in kv
        )
        project = labels.get("com.docker.compose.project", "standalone")
        projects.setdefault(project, []).append(c["Names"])
    return {p: sorted(n) for p, n in sorted(projects.items())}


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--url", default=os.environ.get("KUMA_URL"),
                    help="Kuma base URL (default: $KUMA_URL)")
    ap.add_argument("--dry-run", action="store_true",
                    help="print what would be created; change nothing")
    ap.add_argument("--interval", type=int, default=60,
                    help="check interval in seconds (default 60)")
    ap.add_argument("--skip", action="append", default=[], metavar="NAME",
                    help="container name to leave out (repeatable)")
    args = ap.parse_args()
    if not args.url:
        ap.error("set KUMA_URL or pass --url")

    user = os.environ.get("KUMA_USER", "admin")
    password = os.environ.get("KUMA_PASSWORD") or getpass.getpass(
        f"Kuma password for {user}: ")

    projects = running_containers(set(args.skip))

    api = UptimeKumaApi(args.url)
    v2_compat(api)
    api.login(user, password)
    try:
        run(api, projects, args)
    finally:
        api.disconnect()


def run(api, projects, args):
    dry = args.dry_run
    monitors = api.get_monitors()
    by_name = {m["name"]: m for m in monitors}
    docker_watched = {
        m["docker_container"] for m in monitors
        if m["type"] == MonitorType.DOCKER and m.get("docker_container")
    }
    created = 0
    # Kuma's "Default enabled" notifications are applied by the web form only;
    # API-created monitors get none unless they're passed explicitly.
    default_notifications = [
        n["id"] for n in api.get_notifications() if n.get("isDefault")
    ]
    if not default_notifications:
        print("warning: no default notification set; new monitors won't alert")

    def add(**kw):
        nonlocal created
        label = f"{kw['type'].value:<8} {kw['name']}"
        if dry:
            print(f"  + {label}")
            return None
        mid = api.add_monitor(interval=args.interval,
                              notificationIDList=default_notifications,
                              **kw)["monitorID"]
        print(f"  + {label} (id {mid})")
        created += 1
        return mid

    def group(name):
        existing = by_name.get(name)
        if existing and existing["type"] == MonitorType.GROUP:
            return existing["id"]
        return add(type=MonitorType.GROUP, name=name)

    # 1. Docker host -> socket proxy. Reuse one that already points there;
    #    leave any other hosts alone.
    host = next((h for h in api.get_docker_hosts()
                 if h["dockerDaemon"].rstrip("/") == SOCKET_PROXY), None)
    if host:
        host_id = host["id"]
    else:
        print(f"Docker host: creating socket-proxy -> {SOCKET_PROXY}")
        host_id = None if dry else api.add_docker_host(
            name="socket-proxy", dockerType=DockerType.TCP,
            dockerDaemon=SOCKET_PROXY)["id"]

    # 2. Containers, grouped by Compose project.
    for project, names in projects.items():
        todo = [n for n in names if n not in docker_watched]
        if not todo:
            continue
        print(f"[{project}]")
        parent = group(f"Containers: {project}")
        for name in todo:
            add(type=MonitorType.DOCKER, name=name, docker_container=name,
                docker_host=host_id, parent=parent)

    print("\n(dry run: nothing changed)" if dry
          else f"\nCreated {created} monitor(s).")


if __name__ == "__main__":
    sys.exit(main())
