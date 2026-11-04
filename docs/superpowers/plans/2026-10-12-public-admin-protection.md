# Public Admin Protection Implementation Plan

> For agentic workers: use subagent-driven-development with tests first and independent review. The user approved this plan across UCL and vps-monitoring repositories, then separately authorized the combined live rollout with a skuby7 backup. Live enforcement completed on 2026-10-12. Future changes require separate authorization; no commits, pushes, worktrees or unrelated cleanup.

**Goal:** Block public administrative methods on the UCL Hetzner site while preserving browsing, GET-only browser reload, scheduled data updates and private SSH maintenance.

**Architecture:** Caddy allows GET, HEAD and OPTIONS to reach existing FastAPI; other HTTP methods terminate with 403 before proxy. Public Reload data fetches saved standings/pairs through GET only. Operators use Tailscale SSH and the loopback backend for administrative POSTs.

**Tech stack:** Existing vanilla JS, FastAPI, Caddy, Ansible inventory, pytest and Playwright. No new production dependencies or authentication framework.

## Approved scope

- Public browser Refresh becomes reload-only. Rename it Reload data, with accessible explanation that it fetches stored results and scheduled updates run separately.
- Protect the UCL domain only. Keep backend maintenance APIs unchanged and private loopback access available; preserve scheduler configuration.
- Modify vps-monitoring's Ansible-owned UCL raw_site_body, not unrelated site blocks or live files.
- No credentials embedded in browser, login system, backend model changes, full infrastructure playbook apply, live administrative probes, or server mutation.
- Both repositories contain existing staged/unstaged changes. Preserve them; no stash/reset/staging/commit and no deletion of unrelated artifacts.

## Task 1: Test-first GET-only reload

Files in UCL:
- Modify tests/frontend/test_refresh.py.
- Modify tests/frontend/test_review_gaps.py where old POST-based reload failures are asserted.
- Modify other browser tests only for explicitly obsolete button wording or POST contract; preserve meaningful coverage.

New acceptance:
- Browser reload emits zero POST requests (not just zero /api/refresh requests).
- Only one reload cycle while pending; button disabled until fresh reads complete and restored in every outcome.
- Fresh GET standings and selected-stage pairs, deduplication across Overview/Knockout, invalidated detail/pair caches and superseded requests.
- Preserve view/stage/search/sort/advanced-stat selection; do not show stale analysis as newly current.
- Partial/complete GET failure is visible from each primary view; no false data-update claim.
- Standings loaded at changes only after successful standings fetch.

Verify the new no-POST behavior red against existing implementation before production edits. Existing correct UI/race invariants may remain green.

## Task 2: Frontend implementation

Files:
- frontend/app.js: retain refreshData entrypoint, remove POST /api/refresh and POST-specific state/comments; invalidate browser caches/request generations and await shared GET loaders. Preserve single-flight, disabled button, errors, state and successful-fetch timestamp semantics. Final button label Reload data; reload errors refer to stored-data reload, not forced source updates.
- frontend/index.html: button label Reload data and accessible explanation via aria-describedby or title referencing visible text; no claim of fetching fresh upstream data.

No backend changes. Existing maintenance endpoints and scheduler stay intact. No secret auth token or UI-admin endpoint introduced.

Commands: uv run pytest tests/frontend -q; uv run pytest tests/deploy -q; node --check frontend/app.js. Full uv run pytest separately reports 18 existing backend failures rather than hiding/skipping them.

## Task 3: Caddy test-first policy

Files in /home/linards/.kimaki/projects/vps-monitoring:
- Update tests/test_caddy_inventory_sync.py UCL-specific expected raw body; leave retired-site and unrelated-site expectations unchanged.
- Create tests/test_ucl_read_only_ingress.py for inventory/template contract and isolated Caddy HTTP tests, using a stub upstream, local ephemeral ports and temporary configuration.

Production candidate body:

```caddyfile
encode zstd gzip

@administrative {
    not method GET HEAD OPTIONS
}

handle @administrative {
    respond "Administrative actions require SSH access." 403
}

handle {
    reverse_proxy 127.0.0.1:8003
}
```

Security checks:
- Render exact inventory body and validate/adapt using Caddy before deployment. Integration harness substitutes only the temporary stub upstream and local HTTP listener; keep actual method-policy directives unmodified.
- GET/HEAD/OPTIONS reach stub; status remains backend-dependent, not necessarily 200 on every route.
- POST/PUT/PATCH/DELETE/TRACE terminate with 403; stub records zero forbidden requests.
- Repeat forbidden methods against normal, trailing-slash and encoded-path variants; include spoofed X-Forwarded-For and method-override headers. Block future write routes too.
- The deny rule is UCL-specific; normal unrelated sites remain unchanged and an unrelated fixture site still proxies its POST.
- No real maintenance endpoints are invoked by tests. No TLS issuance, live port binding, secrets or production DB access.
- No hidden skips/xfails if Caddy validation cannot run: report actionable prerequisite blocker. Caddy and Docker binaries are available locally; prefer the installed Caddy binary and bounded child-process lifecycle over requiring a Docker daemon.

Focused command following existing repo convention: uv run --project tools --with jinja2 --with pyyaml python -m pytest tests/test_caddy_inventory_sync.py tests/test_ucl_read_only_ingress.py -q. Ensure actual pytest dependency is available in tools environment; no repository dependency edits unless necessary and explicitly reported.

## Task 4: Inventory implementation and review

- Modify only UCL raw_site_body under ansible/inventories/production/group_vars/vps.yml.
- Preserve upstream 127.0.0.1:8003, site set, all other inventory bytes/values, Caddy template and unrelated existing changes.
- Do not apply Ansible or install/reload live Caddy. Template rendering and local isolated proxy tests only.
- Independent review validates deny-before-proxy semantics, method/header/path bypass resistance, GET-only frontend, cache/race/error correctness and no unrelated changes.
- After code changes follow vps-monitoring graphify update convention, reporting generated graph changes distinctly and never treating unrelated existing dirty graph state as task changes.

## Task 5: Documentation

UCL:
- README.md: public Reload data versus automatic updates and SSH administrative updates.
- DEPLOYMENT.md: selected method policy, private maintenance commands, rollout/rollback ordering; do not label policy deployed yet.
- docs/hetzner-preflight.md: policy selected and locally tested; prior live unauthenticated exposure evidence remains historical until changed.
- docs/superpowers/plans/2026-10-11-hetzner-redeployment.md: security decision selected but live enforcement and other gates incomplete.
- This plan: verification output and remaining gates.

vps-monitoring:
- Create docs/runbooks/ucl-read-only-ingress.md with ownership, tests, targeted validation/apply/rollback and SSH maintenance.
- Keep canonical spec policy consistent via a narrow deviation/decision note if required; no broad monitoring redesign.

## Live rollout order (separate authorization)

1. Refresh topology/configuration evidence; choose/verify private off-host backup destination and resolve data-baseline acceptance/resource gates.
2. Build/smoke candidate image, verify stopped-app snapshot.
3. Replace UCL frontend with GET-only reload first; otherwise proxy-first would break old POST Refresh temporarily.
4. Export current Caddy configuration into protected staging, change only the UCL site block, prove all unrelated content unchanged, validate complete candidate, then separately authorized atomic installation/graceful reload. Never apply a full monitoring playbook that may overwrite unrelated live drift.
5. Verify public reads and method denial through harmless nonexistent /api/__protection_probe__ path. Do not call real historical-sync/cache-clearing endpoints to see whether blocking works.
6. Confirm neighbors, data and scheduler unchanged; verify monitoring or report missing coverage explicitly.

Rollback retains public method protection even when reverting the app image. Old frontend Refresh may be unavailable, which is preferable to reopening administration. Removing ingress protection requires an explicit separate security decision, never automatic rollback of the method gate.

## Security boundaries and limitations

This protects Hetzner public ingress only, not backend authentication. Trusted host/SSH/Docker users retain maintenance access. Container port 8003 must remain loopback-only. Existing read endpoints may compute/cache data internally; GET-only ingress does not claim database read-only operation or comprehensive denial-of-service protection. Any additional deployment path (such as Railway) is outside this protection scope.

## Checklist

- [x] Save approved plan and preserve existing repository states.
- [x] Test engineer writes browser and Caddy tests; confirm meaningful red phase.
- [x] Implement frontend and UCL-only inventory change.
- [x] Browser tests, local Caddy validation and isolated proxy security checks green.
- [x] Independent review accepted; verify every fix.
- [x] Operational docs updated, graph refreshed if required, scoped diffs published.
- [x] Separate live rollout authorization/gates resolved; no production actions during preparation.

References: https://caddyserver.com/docs/caddyfile/matchers and https://caddyserver.com/docs/caddyfile/directives/handle.

## Preparation verification

- UCL frontend/deployment contracts: 120 passed (110 browser, 10 container contracts). Full UCL suite: 177 passed, 18 unchanged backend baseline failures.
- vps-monitoring ingress tests: 22 passed, including actual isolated Caddy validation/HTTP behavior. Combined ingress/inventory suite: 30 passed, 30 pre-existing failures for the other plan's missing cleanup helper/playbook. No skips/xfails.
- Inventory bytes outside UCL raw_site_body were preserved against a pre-edit snapshot. Existing unrelated staged/unstaged changes were not reset, staged or committed.
- Independent code/security and documentation reviews approved; public Reload data makes no POSTs. Backend administrative APIs and scheduler remain unchanged.
- Monitoring code graph refreshed with graphify update . (AST-only, no LLM). Existing graph state was already dirty; generated artifacts are separate from operational changes. Documentation semantic extraction was not run.
- Policy is prepared locally only. No live Caddy reload, administrative probe, container replacement, image build or data snapshot was performed under this preparation task.

## Authorized live enforcement

After preparation, the user separately approved live rollout and the private skuby7 backup destination. On 2026-10-12 the GET-only frontend was deployed first, then the UCL-only method policy was validated against the full current live Caddyfile, installed atomically and gracefully reloaded. POST/PUT/PATCH/DELETE/TRACE on a harmless nonexistent public probe path returned 403; no real administrative POST was executed. Public browser reload issued GET only. Other Caddy bytes, neighboring container IDs, existing Compose/secret configuration and data-bind identity were preserved. The snapshot and active/rollback release identities are documented in `docs/releases/2026-10-12-hetzner-release.md`.
