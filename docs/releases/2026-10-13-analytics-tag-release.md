# Analytics tag cutover — 2026-10-13

## Outcome

The mplytics.eu analytics `<script>` tag (defer) was deployed to https://ucl.mplytics.eu
via the full phase-gated cutover procedure. The live container now runs the analytics
image; the site is healthy and the tag is served publicly. No backend, DB, or API change
was made — this is a frontend-only change.

- Release: `20261013T140544Z-9b7466b-cf5d21eef94a`
- Code commit: `9b7466b2eada7dceec668ccc485ebef9b2b6efb2` (feat: add mplytics.eu analytics script tag)
- Change: `frontend/index.html` — added `<script defer src="https://analytics.mplytics.eu/script.js" data-website-id="25ff9810-7b7a-4898-9a6a-863fd088d3a7"></script>`
- Previous live release: `20261013T102059Z-29a6b2e-d857dba26b13`
- Image: `ucl-solkoff:20261013T140544Z-9b7466b-cf5d21eef94a`, ID `sha256:f98384fe33b9cde9e0954475f9b7f1d9cbd74f4faa84fbd9ce75956ac05eca9a`
- Container created/started `2026-10-13T16:00:26Z`; healthy, restart_count 0
- Compose project/service `ucl-solkoff` / `ucl-solkoff` unchanged; base manifest, env,
  bind `/opt/ucl-solkoff/data → /app/data`, and loopback `127.0.0.1:8003` unchanged
- No Caddy reload required (app-only redeploy); public method restriction unchanged

## Source bundle

Whitelist only (`Dockerfile`, `pyproject.toml`, `uv.lock`, `backend/`, `frontend/`).
`Dockerfile`, `pyproject.toml`, `uv.lock`, and `backend/` are byte-identical to the
previous live release — only `frontend/index.html` changed (+2 lines).

Bundle SHA256: `cf5d21eef94ae306c208344b18498ee67afe6fc2980d48331dd1299fb5a096d5`.

## Consistent backup

Live service snapshot-stopped (exited code 143, clean). No other DuckDB writers active.
Archived `/opt/ucl-solkoff/data` (including `ucl.db`) while stopped:
`/opt/ucl-solkoff/releases/20261013T140544Z-9b7466b-cf5d21eef94a/data-before-apply.tar.gz`.
Copied to private off-host storage on skuby7:
`/home/linards/.local/share/ucl-solkoff/backups/data-before-apply-20261013T140544Z-9b7466b-cf5d21eef94a.tar.gz` (mode 600).
SHA256 (identical VPS + off-host): `8d9cab8f63f5621fcd88e1441861e370f0b1212bbb628e3425dc9b448d9bf7b8`.

## DuckDB read-only validation (via candidate image)

Extracted backup validated read-only with the candidate image's `uv` runtime:
- teams: 509
- matches: 3245
- standings: 36
- solkoff_coefficients: 509
- distinct stages: `LEAGUE_STAGE`, `KNOCKOUT`, `KNOCKOUT_PLAYOFF` (+ legacy `NULL` stage)

## Isolated candidate smoke

- Dependency check (`--network none`): uvicorn started; duckdb/fastapi/httpx/apscheduler/git
  present via the uv project venv; **playwright absent** (dev-only, not installed);
  scheduler disabled without an API key (no sync attempted).
- Health smoke (network, port 8004, temp empty DB, no API key): health
  `{"status":"healthy","database":true}`; root served the full 15,892-byte `index.html`
  **with the analytics tag**; `/app.js` and `/styles.css` both returned 200.
- No sync side effects (scheduler disabled without API key).

## Acceptance (post-cutover)

- HTTPS/cert valid; `https://ucl.mplytics.eu` up.
- `/api/health` (public + loopback) → `{"status":"healthy","database":true}`.
- **Analytics tag present in the live public root** (`analytics.mplytics.eu/script.js`).
- Standings retain expected teams/data; scheduler started (interval 7200s) and synced
  36 teams, 144 matches, 36 standings on first run; Solkoff + Elo calculated;
  historical data already present (1932 matches) — no unwanted full re-sync.
- restart_count 0 (no restart loop); single clean start at `2026-10-13T16:00:26Z`.
- Data mount identity unchanged (`ucl.db` 50,081,792 bytes, root-owned); 14 neighboring
  containers unchanged.

## Rollback

Application-only: same Compose identity, `compose.rollback.yml` override pointing at the
previous image `ucl-solkoff:20261013T102059Z-29a6b2e-d857dba26b13`
(`/opt/ucl-solkoff/releases/20261013T102059Z-29a6b2e-d857dba26b13/compose.release.yml`),
data preserved. Public method restriction is retained (it lives in Caddy, not the app).
