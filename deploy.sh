#!/usr/bin/env bash
# Deploy ucl-solkoff to the Hetzner server alongside OpenClaw.
# Run from the project root: bash openclaw/deploy.sh
set -euo pipefail

SERVER="deploy@46.224.234.243"
APP_DIR="/opt/ucl-solkoff"
OPENCLAW_DIR="/opt/openclaw"

# ── 1. Sync source code ────────────────────────────────────────────────────────
echo "==> Syncing source to $SERVER:$APP_DIR ..."
ssh "$SERVER" "mkdir -p $APP_DIR"
rsync -az --delete \
  --exclude='.venv/' \
  --exclude='__pycache__/' \
  --exclude='*.pyc' \
  --exclude='.pytest_cache/' \
  --exclude='data/' \
  --exclude='.git/' \
  --exclude='.env' \
  --exclude='openclaw/' \
  . "$SERVER:$APP_DIR/"

# ── 2. Upload production .env ──────────────────────────────────────────────────
echo "==> Uploading .env ..."
# Edit the values below before first deploy
cat <<'ENV' | ssh "$SERVER" "cat > $APP_DIR/.env && chmod 600 $APP_DIR/.env"
EXTERNAL_API_KEY=$EXTERNAL_API_KEY
EXTERNAL_API_BASE_URL=https://api.football-data.org/v4
COMPETITION_ID=CL
PORT=8000
DB_PATH=/app/data/ucl.db
UPDATE_INTERVAL=7200
API_CACHE_TTL=7200
API_MIN_REQUEST_INTERVAL=0.2
ENV

# ── 3. Patch Caddyfile (add ucl block if not present) ─────────────────────────
echo "==> Updating Caddyfile ..."
ssh "$SERVER" bash <<'REMOTE'
set -euo pipefail
CADDYFILE=/opt/openclaw/Caddyfile
if grep -q "ucl.mplytics.eu" "$CADDYFILE"; then
  echo "  Caddy block already present, skipping."
else
  cat >> "$CADDYFILE" <<'CADDY'

ucl.mplytics.eu {
    reverse_proxy ucl-solkoff:8000

    header {
        Strict-Transport-Security "max-age=63072000; includeSubDomains; preload"
        X-Content-Type-Options "nosniff"
        X-Frame-Options "SAMEORIGIN"
        Referrer-Policy "strict-origin-when-cross-origin"
        -Server
    }

    log {
        output file /data/ucl-access.log {
            roll_size 10mb
            roll_keep 5
        }
    }
}
CADDY
  echo "  Caddy block added."
fi
REMOTE

# ── 4. Patch docker-compose.yml (add ucl-solkoff service if not present) ──────
echo "==> Updating docker-compose.yml ..."
ssh "$SERVER" bash <<'REMOTE'
set -euo pipefail
COMPOSEFILE=/opt/openclaw/docker-compose.yml
if grep -q "ucl-solkoff" "$COMPOSEFILE"; then
  echo "  Service already present, skipping."
else
  # Insert service block before the 'volumes:' line
  python3 - <<'PY'
import re, sys

path = "/opt/openclaw/docker-compose.yml"
with open(path) as f:
    content = f.read()

service_block = """
  ucl-solkoff:
    build:
      context: /opt/ucl-solkoff
      dockerfile: Dockerfile
    image: ucl-solkoff:latest
    restart: unless-stopped
    env_file: /opt/ucl-solkoff/.env
    volumes:
      - ucl_data:/app/data
    networks:
      - frontend
    healthcheck:
      test: ["CMD", "python3", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/health', timeout=5)"]
      interval: 60s
      timeout: 10s
      retries: 3
      start_period: 90s

"""

# Add volume declaration before 'volumes:' section
volume_decl = "  ucl_data:\n"

# Insert service before 'volumes:' line
content = re.sub(r'^(volumes:)', service_block + r'\1', content, flags=re.MULTILINE)
# Insert volume declaration after 'volumes:' line
content = re.sub(r'^(volumes:\n)', r'\1' + volume_decl, content, flags=re.MULTILINE)

with open(path, "w") as f:
    f.write(content)
print("  Service and volume added.")
PY
fi
REMOTE

# ── 5. Build image and start service ──────────────────────────────────────────
echo "==> Building image on server ..."
ssh "$SERVER" "cd $OPENCLAW_DIR && docker compose build ucl-solkoff"

echo "==> Starting ucl-solkoff ..."
ssh "$SERVER" "cd $OPENCLAW_DIR && docker compose up -d ucl-solkoff"

echo "==> Reloading Caddy ..."
ssh "$SERVER" "cd $OPENCLAW_DIR && docker compose exec caddy caddy reload --config /etc/caddy/Caddyfile"

# ── 6. Smoke test ─────────────────────────────────────────────────────────────
echo "==> Smoke test ..."
sleep 5
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" https://ucl.mplytics.eu)
if [ "$HTTP_CODE" = "200" ]; then
  echo "  OK — https://ucl.mplytics.eu returned $HTTP_CODE"
else
  echo "  WARNING — https://ucl.mplytics.eu returned $HTTP_CODE (app may still be starting)"
fi

echo ""
echo "Done. Monitor with:"
echo "  ssh $SERVER 'cd $OPENCLAW_DIR && docker compose logs -f ucl-solkoff'"
