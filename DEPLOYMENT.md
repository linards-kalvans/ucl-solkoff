# Deployment

## Overview

The app runs as a Docker container on a Hetzner CX23 server alongside OpenClaw.
Caddy handles TLS (auto Let's Encrypt) and reverse proxying.

| URL | Service |
|-----|---------|
| https://ucl.mplytics.eu | UCL Solkoff (this app) |
| https://claw.mplytics.eu | OpenClaw (same server) |

**Server:** `46.224.234.243` (Hetzner nbg1) — SSH as `deploy@46.224.234.243`

---

## First deploy

### Prerequisites

- SSH access configured (`~/.ssh/config` or key loaded in agent)
- DNS record for `ucl.mplytics.eu` pointing to the server IP

### 1. Create the app directory on the server (once only)

```bash
ssh deploy@46.224.234.243 "sudo mkdir -p /opt/ucl-solkoff && sudo chown deploy:deploy /opt/ucl-solkoff"
```

### 2. Run the deploy script

```bash
cd /path/to/ucl-solkoff
bash openclaw/deploy.sh
```

The script does everything in one shot:

1. Rsyncs source to `/opt/ucl-solkoff/` on the server
2. Writes `/opt/ucl-solkoff/.env` with production config
3. Appends the `ucl.mplytics.eu` block to `/opt/openclaw/Caddyfile` (skipped if already present)
4. Injects the `ucl-solkoff` service + `ucl_data` volume into `/opt/openclaw/docker-compose.yml` (skipped if already present)
5. Builds the Docker image on the server
6. Starts the container (`docker compose up -d ucl-solkoff`)
7. Reloads Caddy to pick up the new vhost
8. Smoke-tests `https://ucl.mplytics.eu`

Expected output ends with `HTTP 200`.

---

## Redeploying after code changes

```bash
cd /path/to/ucl-solkoff
bash openclaw/deploy.sh
```

The rsync + build steps are idempotent. Caddy and compose patching are skipped on subsequent runs.
The container is replaced in-place with the rebuilt image.

---

## Server layout

```
/opt/openclaw/              ← OpenClaw stack (Caddy lives here)
├── docker-compose.yml      ← ucl-solkoff service is added here
├── Caddyfile               ← ucl.mplytics.eu block is added here
└── .env

/opt/ucl-solkoff/           ← app source (rsynced from local)
├── Dockerfile
├── backend/
├── frontend/
├── pyproject.toml
├── uv.lock
└── .env                    ← production secrets (chmod 600)
```

Data (DuckDB + GitHub history cache) persists in the `ucl_data` Docker named volume,
mounted at `/app/data` inside the container. It survives container restarts and rebuilds.

---

## Useful commands

```bash
# Tail live logs
ssh deploy@46.224.234.243 "cd /opt/openclaw && docker compose logs -f ucl-solkoff"

# Container status and health
ssh deploy@46.224.234.243 "cd /opt/openclaw && docker compose ps ucl-solkoff"

# Open a shell inside the container
ssh deploy@46.224.234.243 "cd /opt/openclaw && docker compose exec ucl-solkoff bash"

# Trigger a manual data refresh
curl -X POST https://ucl.mplytics.eu/api/refresh

# Restart without rebuilding
ssh deploy@46.224.234.243 "cd /opt/openclaw && docker compose restart ucl-solkoff"
```

---

## Environment variables

Managed in `/opt/ucl-solkoff/.env` on the server (written by `openclaw/deploy.sh`).

| Variable | Description |
|----------|-------------|
| `EXTERNAL_API_KEY` | football-data.org API key |
| `EXTERNAL_API_BASE_URL` | API base URL |
| `COMPETITION_ID` | `CL` (Champions League) |
| `DB_PATH` | `/app/data/ucl.db` (inside container) |
| `UPDATE_INTERVAL` | Scheduler interval in seconds (default 7200) |
| `API_CACHE_TTL` | API cache TTL in seconds (default 7200) |
| `API_MIN_REQUEST_INTERVAL` | Minimum delay between API requests in seconds |

To change a value: edit the `cat <<'ENV'` heredoc in `openclaw/deploy.sh`, then redeploy.
To change it immediately without redeploying: edit the file on the server and `docker compose restart ucl-solkoff`.

---

## Modifying Caddy or compose config after initial deploy

The deploy script only patches these files once. For subsequent changes:

```bash
# Edit Caddyfile directly on server, then reload
ssh deploy@46.224.234.243 "nano /opt/openclaw/Caddyfile"
ssh deploy@46.224.234.243 "cd /opt/openclaw && docker compose exec caddy caddy reload --config /etc/caddy/Caddyfile"

# Edit compose file directly on server, then apply
ssh deploy@46.224.234.243 "nano /opt/openclaw/docker-compose.yml"
ssh deploy@46.224.234.243 "cd /opt/openclaw && docker compose up -d ucl-solkoff"
```

---

## Troubleshooting

**Container not starting**
```bash
ssh deploy@46.224.234.243 "cd /opt/openclaw && docker compose logs ucl-solkoff --tail=50"
```

**Caddy not routing `ucl.mplytics.eu`**
```bash
ssh deploy@46.224.234.243 "cd /opt/openclaw && docker compose logs caddy --tail=20"
ssh deploy@46.224.234.243 "cd /opt/openclaw && docker compose exec caddy caddy reload --config /etc/caddy/Caddyfile"
```

**Empty standings / data not loading**
The app fetches live data on startup and then every `UPDATE_INTERVAL` seconds.
Check logs for `HTTP 403` (invalid API key) or `HTTP 429` (rate limit exceeded).

**TLS certificate not issued**
Caddy obtains Let's Encrypt certificates automatically on first request.
Requires ports 80 and 443 open and DNS resolving to the server.
Certificate issuance takes up to ~30 seconds on first hit.
