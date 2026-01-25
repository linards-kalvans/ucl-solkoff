# UEFA Champions League Solkoff Coefficient App

A web application that displays UEFA Champions League standings with calculated Solkoff coefficients (sum of opponent points) as a tiebreaker metric.

## Features

- **Real-time Data**: Fetches latest Champions League data from football-data.org API
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

4. Edit `.env` and add your football-data.org API key:
   - Get a free API key from [football-data.org](https://www.football-data.org/client/register)
   - Replace `your_api_key_here` with your actual API key
   - **Important**: Without a valid API key, you'll get 403 Forbidden errors**

See [SETUP.md](SETUP.md) for detailed setup instructions and troubleshooting.

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
- `GET /api/standings` - Get standings with Solkoff coefficients
- `POST /api/refresh` - Manually trigger data refresh
- `POST /api/cache/clear` - Clear API response cache

## Configuration

Environment variables (`.env`):

- `EXTERNAL_API_KEY` - football-data.org API key (required)
- `EXTERNAL_API_BASE_URL` - API base URL (default: https://api.football-data.org/v4)
- `COMPETITION_ID` - Competition ID (default: "CL" for Champions League)
- `PORT` - Backend server port (default: 8000)
- `DB_PATH` - DuckDB database path (default: ./data/ucl.db)
- `UPDATE_INTERVAL` - Scheduler interval in seconds (default: 3600 = 1 hour)
- `API_CACHE_TTL` - API response cache time-to-live in seconds (default: 3600 = 1 hour)
- `API_MIN_REQUEST_INTERVAL` - Minimum seconds between API requests (default: 0.1 = 100ms)

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

