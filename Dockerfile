FROM ghcr.io/astral-sh/uv:python3.11-bookworm-slim

# git is required by gitpython to clone the openfootball historical data repo
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install dependencies from lockfile before copying source (layer cache)
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

# Copy application source
COPY backend/ ./backend/
COPY frontend/ ./frontend/

# Persistent data lives here (DuckDB + GitHub cache) — mount a volume in production
RUN mkdir -p /app/data

EXPOSE 8000

CMD ["uv", "run", "uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
