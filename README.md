# uptime-notify

[Uptime Kuma](https://github.com/louislam/uptime-kuma) with alerts delivered over
[Signal](https://signal.org), packaged as one Docker Compose stack and served
privately on your [Tailscale](https://tailscale.com) tailnet.

| Service | Image | Role |
|---|---|---|
| `uptime-kuma` | `louislam/uptime-kuma:2` | Monitoring UI and checks |
| `signal-api` | `bbernhard/signal-cli-rest-api` | Sends Kuma's alerts from a Signal number |
| `socket-proxy` | `tecnativa/docker-socket-proxy` | Read-only Docker API for "Docker Container" monitors |
| `caddy` | built from `./caddy` | Joins the tailnet and serves Kuma over HTTPS |

## Security model

- **No host ports are published.** Kuma is reachable only at
  `https://<TS_HOSTNAME>.<TS_TAILNET>`, so only devices on your tailnet can open it.
  Tailscale provides the HTTPS certificate automatically.
- **The Signal API has no authentication.** Anything that can reach it can send
  messages as your number. It sits on an `internal` network that only Kuma
  shares, and you reach it for admin tasks through `docker compose exec`.
- **Kuma never touches the Docker socket.** The socket proxy allows read-only
  container queries and rejects every write.

## Requirements

- Docker Engine with the Compose plugin (v2.20+)
- A Tailscale tailnet with MagicDNS and HTTPS certificates enabled
  (admin console → DNS)
- A phone number for the Signal sender that can receive an SMS or voice call.
  Use a dedicated number: registering it here signs it out of Signal on any
  phone that already uses it as its primary number. You can link instead to
  keep using the phone; see below.

## Setup

### 1. Tailscale credentials

1. In your tailnet policy file, make sure the tag exists:
   ```json
   "tagOwners": { "tag:container": ["autogroup:admin"] }
   ```
2. Admin console → Settings → OAuth clients: create a client with the
   **Auth Keys → Write** scope and tag `tag:container`. Copy the secret
   (`tskey-client-...`).
   A plain non-ephemeral auth key (`tskey-auth-...`) also works.

### 2. Configure and start

```sh
git clone <this repo> uptime-notify && cd uptime-notify
cp .env.example .env && chmod 600 .env
$EDITOR .env                       # TS_AUTHKEY, TS_TAILNET at minimum
mkdir -p data/kuma data/signal
sudo chown 1000:1000 data/signal   # signal-api runs as uid 1000 (skip if that's you)
docker compose up -d --build
```

The first start of Kuma takes up to a few minutes. Caddy starts once Kuma
reports healthy, then the new machine appears in your Tailscale admin console.
Open `https://<TS_HOSTNAME>.<TS_TAILNET>` and create the admin account.

### 3. Register the Signal number

All of the following runs inside the container, because the API isn't exposed.
Replace `+15551234567` with your number in international format.

```sh
NUM=+15551234567
api() { docker compose exec -T signal-api curl -sS "$@"; }
```

1. Solve a captcha at <https://signalcaptchas.org/registration/generate.html>.
   When it finishes, right-click "Open Signal" and copy the link. It starts
   with `signalcaptcha://`. Use it right away, because it expires quickly.
2. Request a verification code by SMS. For a landline, add `"use_voice": true`:
   ```sh
   api -X POST -H 'Content-Type: application/json' \
     -d '{"captcha":"signalcaptcha://..."}' \
     http://localhost:8080/v1/register/$NUM
   ```
3. Verify with the code you received:
   ```sh
   api -X POST http://localhost:8080/v1/register/$NUM/verify/123456
   ```
4. Send a test message:
   ```sh
   api -X POST -H 'Content-Type: application/json' \
     -d "{\"message\":\"hello from uptime-notify\",\"number\":\"$NUM\",\"recipients\":[\"+15557654321\"]}" \
     http://localhost:8080/v2/send
   ```

**To link instead of registering**, add the API as a secondary device of an
existing Signal account:

```sh
api 'http://localhost:8080/v1/qrcodelink?device_name=uptime-notify' > link.png
```

Then scan `link.png` from Signal → Settings → Linked devices, and delete the
file afterwards.

### 4. Point Kuma at Signal

In Kuma: Settings → Notifications → Setup Notification:

| Field | Value |
|---|---|
| Notification Type | Signal |
| Post URL | `http://signal-api:8080/v2/send` |
| Number | your sender number, e.g. `+15551234567` |
| Recipients | comma-separated numbers (or group IDs from `GET /v1/groups/<number>`) |

Click **Test**, and tick "Default enabled" if you want it on new monitors.

### 5. (Optional) Docker container monitors

Settings → Docker Hosts → add a host:
- Connection type: **TCP / HTTP**
- Docker Daemon: `http://socket-proxy:2375`

Then create monitors of type "Docker Container", or let the script below
create them all.

#### Add every running container automatically

`scripts/add_container_monitors.py` reads `docker ps` on the host and creates a
group per Compose project ("Containers: &lt;project&gt;") with a Docker Container
monitor for each running container. It also adds the Docker host above if it's
missing. It never edits or deletes existing monitors, so re-run it whenever you
add containers. Kuma's own container is skipped.

It needs [uv](https://docs.astral.sh/uv/), which installs the
`uptime-kuma-api` dependency on the fly. Run it on the Docker host:

```sh
export KUMA_URL=https://uptime.tail1234.ts.net   # KUMA_USER defaults to admin
uv run scripts/add_container_monitors.py --dry-run
uv run scripts/add_container_monitors.py         # prompts for the password
```

Options: `--interval 60` sets the check interval, and `--skip NAME`
(repeatable) leaves out a container.

Set up the Signal notification first and tick **Default enabled**. Monitors
created through the API only get default notifications if the script attaches
them, and it attaches whichever notifications are marked default when it runs.

## Operations

```sh
docker compose pull && docker compose up -d --build   # update
docker compose logs -f signal-api                      # debug alert delivery
```

**Back up `./data/`.** `data/kuma` holds your monitors and history. Stop the
stack or use Kuma's backup feature for a consistent copy. `data/signal` holds
the account keys, and losing it means registering the number again.

## Customizing

- **Reach Kuma without Tailscale (LAN or localhost only).** Add a
  `compose.override.yaml`:
  ```yaml
  services:
    uptime-kuma:
      ports: ["127.0.0.1:3001:3001"]
  ```
  and run `docker compose up -d uptime-kuma signal-api socket-proxy`.
  Don't publish `signal-api`.
- **Use another Docker host for the socket proxy.** Set `DOCKER_SOCKET` in
  `.env`, for example `/run/user/1000/docker.sock` for rootless Docker.
- **Pin the build.** Set `CADDY_TAILSCALE_REF` to a commit SHA of
  `tailscale/caddy-tailscale`.
