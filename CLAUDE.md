# UCL Solkoff — CLAUDE.md

## Project Overview

UEFA Champions League standings web app displaying Solkoff coefficients (average PPG of opponents faced) as a strength-of-schedule tiebreaker. Also shows knockout-stage playoff pairs with common-opponent analysis.

## Tech Stack

- **Backend:** Python 3.11+, FastAPI, DuckDB, APScheduler, httpx
- **Frontend:** Vanilla JS + HTML + CSS, served as static files by FastAPI
- **Package manager:** `uv` — always use `uv run` to execute Python commands
- **Data sources:** football-data.org API (live data) + openfootball GitHub repo (historical data)

## Commands

```bash
# Development server
uv run uvicorn backend.main:app --reload

# Run tests
uv run pytest
```

## Deployment

Deployed on [Railway](https://railway.app). Push to `main` triggers auto-redeploy. See [DEPLOYMENT.md](DEPLOYMENT.md) for details.
