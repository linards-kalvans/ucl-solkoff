"""Static tests for the production container contract.

These tests parse the tracked Dockerfile (and pyproject.toml) directly.
They never build or run a container: no Docker daemon, browser, or network
dependency, so they run in the default normal suite without skips.

Contract under test (see docs/superpowers/plans/2026-10-11-hetzner-redeployment.md):
- build-time install is lockfile-frozen and excludes the dev dependency group
  (the default group contains Playwright, which must never land in runtime);
- startup uses the preinstalled production environment and must not re-sync,
  so the dev group cannot be pulled in at runtime;
- startup runs the FastAPI app via uvicorn on 0.0.0.0:8000;
- startup has no auto-reload and exactly one application worker (the embedded
  APScheduler and DuckDB writer are not scaled horizontally).

The Dockerfile is tokenized (shell words for RUN, JSON for CMD) instead of
matching whole-line whitespace snapshots, so harmless formatting/whitespace
changes do not break the suite.
"""
from __future__ import annotations

import json
import shlex
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DOCKERFILE = REPO_ROOT / "Dockerfile"
PYPROJECT = REPO_ROOT / "pyproject.toml"


# --------------------------------------------------------------------------- #
# Parsing helpers (meaningful, whitespace-tolerant)
# --------------------------------------------------------------------------- #
def _logical_lines(text: str) -> list[str]:
    """Split on newlines, joining backslash-continuations into one line."""
    logical: list[str] = []
    buf = ""
    for raw in text.splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.endswith("\\"):
            buf += stripped[:-1].rstrip() + " "
            continue
        buf += stripped
        logical.append(buf)
        buf = ""
    if buf:
        logical.append(buf)
    return logical


def _directives() -> list[tuple[str, str]]:
    """Yield (INSTRUCTION, argument-string) pairs, order preserved."""
    out = []
    for line in _logical_lines(DOCKERFILE.read_text(encoding="utf-8")):
        parts = line.split(None, 1)
        if len(parts) == 2:
            out.append((parts[0].upper(), parts[1]))
    return out


def _cmd_argument() -> str:
    for instruction, argument in _directives():
        if instruction == "CMD":
            return argument
    raise AssertionError("Dockerfile must define a CMD instruction")


def _cmd_tokens() -> list[str]:
    """CMD argument parsed as JSON exec-form into a token list."""
    raw = _cmd_argument()
    parsed = json.loads(raw)
    if not isinstance(parsed, list):
        raise AssertionError("CMD must be JSON exec-form: a list of strings")
    if not all(isinstance(tok, str) for tok in parsed):
        raise AssertionError("CMD exec-form tokens must all be strings")
    return parsed


def _uv_sync_run_tokens() -> list[str]:
    """Shell words of the RUN instruction that invokes ``uv sync``."""
    for instruction, argument in _directives():
        if instruction != "RUN":
            continue
        try:
            tokens = shlex.split(argument)
        except ValueError:
            continue
        if "uv" in tokens and "sync" in tokens:
            return tokens
    raise AssertionError("Dockerfile must install dependencies with `uv sync`")


def _flag_value(tokens: list[str], flag: str) -> str | None:
    if flag not in tokens:
        return None
    index = tokens.index(flag)
    if index + 1 >= len(tokens):
        raise AssertionError(f"flag {flag!r} must be followed by a value")
    return tokens[index + 1]


# --------------------------------------------------------------------------- #
# Build-time install contract
# --------------------------------------------------------------------------- #
def test_run_uv_sync_uses_frozen_lockfile():
    tokens = _uv_sync_run_tokens()
    assert "--frozen" in tokens, (
        "build install must be lockfile-frozen (--frozen) so the image "
        "matches uv.lock"
    )


def test_run_uv_sync_excludes_dev_dependency_group():
    tokens = _uv_sync_run_tokens()
    assert "--no-dev" in tokens, (
        "build install must exclude the default dev group (--no-dev), which "
        "contains Playwright"
    )


def test_playwright_is_dev_group_only_not_production_dependency():
    """The dev-group exclusion is meaningful: no --no-dev would install Playwright."""
    text = PYPROJECT.read_text(encoding="utf-8")
    import tomllib

    data = tomllib.loads(text)
    dev = " ".join(data["dependency-groups"]["dev"])
    dependencies = " ".join(data["project"]["dependencies"])
    assert "playwright" in dev, (
        "pyproject must declare playwright in the dev dependency group so the "
        "container exclusion is meaningful"
    )
    assert "playwright" not in dependencies, (
        "playwright must not be a production dependency"
    )


# --------------------------------------------------------------------------- #
# Startup contract (runtime uses the preinstalled environment)
# --------------------------------------------------------------------------- #
def test_startup_disables_runtime_sync():
    tokens = _cmd_tokens()
    assert "uv" in tokens and "run" in tokens, (
        "startup must run through `uv run`"
    )
    assert "--no-sync" in tokens, (
        "startup must pass --no-sync so the preinstalled production "
        "environment is reused and the default dev group (Playwright) is "
        "never installed at runtime"
    )


def test_startup_uses_preinstalled_environment_before_uvicorn():
    """``--no-sync`` must sit among the uv arguments, before the uvicorn command."""
    tokens = _cmd_tokens()
    run_index = tokens.index("run")
    no_sync_index = tokens.index("--no-sync")
    uvicorn_index = tokens.index("uvicorn")
    assert run_index < no_sync_index < uvicorn_index, (
        "--no-sync must be passed as a `uv run` argument before the uvicorn "
        "invocation so sync is skipped (harmless additional uv flags allowed)"
    )


# --------------------------------------------------------------------------- #
# Application entrypoint contract
# --------------------------------------------------------------------------- #
def test_cmd_is_json_exec_form_not_shell_string():
    raw = _cmd_argument()
    assert raw.lstrip().startswith("["), (
        "CMD must use JSON exec-form (bracket list), not shell-form string, "
        "so `uv run --no-sync` is executed without a shell"
    )
    _cmd_tokens()  # also validates the JSON parses


def test_startup_targets_fastapi_app_module():
    assert "backend.main:app" in _cmd_tokens(), (
        "uvicorn must start the FastAPI app at backend.main:app"
    )


def test_startup_binds_all_interfaces_on_port_8000():
    tokens = _cmd_tokens()
    assert _flag_value(tokens, "--host") == "0.0.0.0", (
        "uvicorn must bind 0.0.0.0 as host"
    )
    assert _flag_value(tokens, "--port") == "8000", (
        "uvicorn must listen on port 8000"
    )


# --------------------------------------------------------------------------- #
# Process shape contract (no reload, single worker)
# --------------------------------------------------------------------------- #
def test_startup_has_no_autoreload():
    assert "--reload" not in _cmd_tokens(), (
        "production startup must not enable uvicorn auto-reload"
    )


def test_startup_has_single_worker_only():
    tokens = _cmd_tokens()
    workers = _flag_value(tokens, "--workers")
    if workers is not None:
        assert workers == "1", (
            "production startup must run exactly one worker; embedded "
            "scheduler and DuckDB writer are not scaled horizontally"
        )
    # absence of --workers also satisfies the contract (uvicorn default is 1)