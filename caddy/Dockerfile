# Caddy with the Tailscale plugin, so the proxy joins the tailnet directly
# (its own node, MagicDNS name, and automatic HTTPS).
#
# Built rather than pulled: ghcr.io/tailscale/caddy-tailscale publishes only a
# moving `main` tag. Set CADDY_TAILSCALE_REF in .env to a commit SHA to pin it.
ARG CADDY_VERSION=2.11.4

FROM caddy:${CADDY_VERSION}-builder-alpine AS builder
ARG CADDY_TAILSCALE_REF=main
RUN xcaddy build --with github.com/tailscale/caddy-tailscale@${CADDY_TAILSCALE_REF}

FROM caddy:${CADDY_VERSION}-alpine
COPY --from=builder /usr/bin/caddy /usr/bin/caddy
COPY Caddyfile /etc/caddy/Caddyfile
