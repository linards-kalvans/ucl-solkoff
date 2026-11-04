# UEFA Champions League Solkoff Coefficient App

A web application that displays UEFA Champions League, Europa League and Conference League standings with calculated Solkoff coefficients (average opponent points per game) as a strength-of-schedule metric.

## Features

- **European Competitions**: Select Champions League (`CL`, 8 league games), Europa League (`EL`, 8) or Conference League (`UCL`, 6; the provider's code, not Champions League)
- **Provider Routing**: Champions League uses football-data.org; Europa and Conference League use ESPN public JSON without an ESPN key
- **Season Switching**: Explore stored new-format seasons from 2024/25 onward within each competition
- **Solkoff Coefficients**: Pre-calculated tiebreaker metric showing strength of schedule
- **Persistent Storage**: Uses DuckDB for fast, lightweight data storage
- **Automatic Updates**: Background scheduler refreshes data periodically
- **Modern UI**: Clean, responsive web interface with sortable tables

## Architecture

- **Backend**: Python/FastAPI with DuckDB database
- **Frontend**: Vanilla JavaScript with modern CSS
- **Package Management**: uv for fast Python dependency management

## Setup

### Prerequisites

- Python 3.11+
- [uv](https://github.com/astral-sh/uv) package manager

### Installation

1. Clone the repository:
```bash
cd ucl-solkoff
```

2. Install dependencies (already done if using uv):
```bash
uv sync
```

3. Create `.env` file:
```bash
cat > .env << EOF
EXTERNAL_API_KEY=your_api_key_here
EXTERNAL_API_BASE_URL=https://api.football-data.org/v4
COMPETITION_ID=CL
PORT=8000
DB_PATH=./data/ucl.db
UPDATE_INTERVAL=3600
EOF
```

4. For CL refreshes, edit `.env` and add your football-data.org API key:
   - Get a free API key from [football-data.org](https://www.football-data.org/client/register)
   - Replace `your_api_key_here` with your actual API key
   - Without `EXTERNAL_API_KEY`, CL network refresh is disabled; EL/UCL ESPN refresh and GET views of stored data still work. An invalid key can cause CL authentication failures.

See [SETUP.md](SETUP.md) for detailed setup instructions and troubleshooting.

EL/UCL use ESPN for current and archived seasons from 2024/25 onward; older archive sources are retained. ESPN endpoints are unofficial, and public accessibility does not establish unrestricted licensing. Imports fail closed on unresolved legacy data. [Offline reconciliation tooling](docs/espn-reconciliation.md) builds and verifies new candidates without changing the source. **Production rollout has not occurred or been approved**; fresh-snapshot reconciliation, terms/security review and restorable schema/data plus image rollback remain future rollout requirements. See [ESPN provider behavior](docs/espn-provider.md).

The [main-stage copy completion](docs/espn-main-stage-copy.md) was independently accepted on October 26: 151 reviewed club roots, all 936 recorded main-stage events, 3,839 total matches and six 36-club EL/Conference views, including read-only API and Chromium checks. This supersedes the partial [correction rehearsal](docs/espn-correction-rehearsal.md) status, not its history. Acceptance is copy-only, using recorded October 15/16 captures—not production readiness or fresh-feed validation.

### Running the Application

1. Start the backend server:
```bash
uv run uvicorn backend.main:app --reload
```

The API will be available at `http://localhost:8000`

2. Open the frontend:
   - Open `frontend/index.html` in your browser, or
   - Serve it with a simple HTTP server:
   ```bash
   cd frontend
   python -m http.server 8080
   ```
   Then open `http://localhost:8080`

### Running Tests

Run all tests:
```bash
uv run pytest
```

Run tests for a specific module:
```bash
uv run pytest tests/backend/test_database.py
```

Run with verbose output:
```bash
uv run pytest -v
```

## API Endpoints

- `GET /` - Root endpoint
- `GET /api/health` - Health check
- `GET /api/competitions` - Supported competition metadata, configured default and live-data availability
- `GET /api/seasons?competition=EL` - Selectable seasons for a competition
- `GET /api/standings` - Get standings with Solkoff coefficients
- `POST /api/refresh` - Manually trigger data refresh
- `POST /api/cache/clear` - Clear API response cache

Season-dependent data GETs accept optional `competition` (case-insensitive) and `season` (start year) parameters, for example `/api/standings?competition=UCL&season=2024`. Omitted competition uses the validated `COMPETITION_ID`; an unsupported explicit code returns HTTP 400. Supported competitions with no stored data do not fall back to another competition. See [competition contracts and data semantics](docs/competitions.md) and [season switching](docs/seasons.md) for the complete route list and behavior.

## Configuration

Environment variables (`.env`):

- `EXTERNAL_API_KEY` - football-data.org API key (needed for CL network refresh, not ESPN or stored-data GETs)
- `EXTERNAL_API_BASE_URL` - football-data.org API base URL (default: https://api.football-data.org/v4; does not configure ESPN)
- `COMPETITION_ID` - Default request/UI competition and legacy snapshot migration attribution: `CL`, `EL` or `UCL` (case-insensitive; missing, blank or unsupported values resolve to `CL`)
- `PORT` - Backend server port (default: 8000)
- `DB_PATH` - DuckDB database path (default: ./data/ucl.db)
- `UPDATE_INTERVAL` - Scheduler interval in seconds (default: 3600 = 1 hour)
- `API_CACHE_TTL` - football-data.org response cache time-to-live in seconds (default: 3600 = 1 hour); ESPN currently uses its Python client default of 3600 seconds
- `API_MIN_REQUEST_INTERVAL` - Minimum seconds between football-data.org requests (default: 0.1 = 100ms); ESPN independently enforces at least 0.5 seconds

Application startup explicitly configures refreshes for all three competitions; `COMPETITION_ID` does not restrict that list. The optional comma-separated `COMPETITIONS` environment variable is read only when constructing `DataScheduler` directly without an explicit `competitions` list; it does not control the application's startup list. See [refresh configuration](docs/competitions.md#refresh-configuration).

Before rolling out against an existing database, back it up and confirm that `COMPETITION_ID` identifies its legacy live snapshots. Initialization atomically migrates standings and coefficients to competition-keyed, live-only snapshots; historical tables are reconstructed from stored matches, not archived snapshots. Matches with NULL competition provenance remain unknown. Elo intentionally pools results across CL, EL and UCL. See [storage and migration](docs/competitions.md#storage-and-migration).

## Project Structure

```
ucl-solkoff/
├── backend/
│   ├── __init__.py
│   ├── main.py              # FastAPI application
│   ├── database.py           # DuckDB connection and schema
│   ├── api_client.py         # External API client
│   ├── data_service.py       # Data ingestion service
│   ├── solkoff_calculator.py # Solkoff coefficient calculator
│   └── scheduler.py          # Background scheduler
├── frontend/
│   ├── index.html
│   ├── styles.css
│   └── app.js
├── tests/
│   └── backend/
│       ├── test_database.py
│       ├── test_api_client.py
│       ├── test_data_service.py
│       ├── test_solkoff_calculator.py
│       ├── test_scheduler.py
│       └── test_main.py
├── data/                     # Database files (gitignored)
├── pyproject.toml            # Project configuration
└── README.md
```

## Testing

The project includes comprehensive tests for each component:

- **Database Tests**: Schema creation, table structure, data operations
- **API Client Tests**: HTTP requests, error handling, data parsing
- **Data Service Tests**: Data synchronization, team/matches/standings sync
- **Solkoff Calculator Tests**: Coefficient calculation, opponent identification
- **Scheduler Tests**: Background jobs, update triggers
- **API Endpoint Tests**: FastAPI routes, response formatting

Run the test suite to verify all components work correctly.

## License

MIT
