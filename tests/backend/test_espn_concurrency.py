"""Concurrency regressions for the shared DuckDB connection.

The application shares one DuckDB connection across the scheduler, the startup
history thread and request handlers. ESPN writes a multi-statement import inside
a single transaction; without a shared lock a competing thread's commit (here an
``APICache`` write) could interleave and commit a partial import.

These tests pause an ESPN import *inside* its transaction using deterministic
``threading.Event`` handshakes (bounded ``Event.wait`` used only for deadlock
detection, never a fixed sleep or a performance budget) and prove that:

* a competing commit blocks while the transaction is open and only finishes
  after it commits/rolls back;
* a forced ESPN rollback leaves no partial mappings/matches/standings behind;
* a successful import still commits and stays idempotent on a repeat sync;
* the lock is released on the error path.

The pause point is deliberately the *first fixture write* inside the
transaction: the invariant under test is that no competing commit can start
while the shared lock is held. Reaching it needs only the 36 standings clubs plus
one fixture, so the handshake measures the invariant rather than the incidental
cost of a full 189-fixture import (which the separate idempotency test still
exercises end to end).

All databases are fresh temporary DuckDB files; no production DB, network or
HTTP is touched.
"""
from __future__ import annotations

import threading

import pytest

from backend.api_cache import APICache
from backend.database import Database
from backend.espn_client import ESPNClient
from backend.espn_service import ESPNDataService
from tests.backend import espn_payloads as P


# Generous bound used only to detect a deadlocked/never-reached handshake. Not a
# performance budget for any import work: the pause point requires a tiny,
# fixed amount of work.
HANDSHAKE_TIMEOUT = 30.0


class FakeESPN(ESPNClient):
    """Offline client returning injected standings/scoreboard payloads."""

    def __init__(self, standings, events):
        super().__init__(cache=None)
        self._standings = standings
        self._events = events

    def _get_standings(self, slug, season_year):
        return self._standings

    def _get_scoreboard(self, slug, year_arg):
        year = str(year_arg)
        events = [e for e in self._events if e["date"][:4] == year]
        return P.scoreboard_payload(events, slug=slug)


def el_client(season_year=2024):
    return FakeESPN(
        P.standings_payload("EL", season_year),
        P.el_season_events(season_year),
    )


def count(db, table, where="", params=()):
    row = db.fetchone(f"SELECT COUNT(*) FROM {table} {where}", params)
    return row[0] if row else 0


def _pin_current_season(monkeypatch):
    # Pin "current" to 2024 so the import writes the live standings snapshot
    # instead of treating the season as archived.
    monkeypatch.setattr("backend.espn_service.current_season_year", lambda: 2024)


class PausingESPNService(ESPNDataService):
    """ESPN service that pauses once, inside the open transaction, right after
    the first fixture row (and its provider mapping) is written.

    ``_resolve_match`` calls ``super()`` first, so a partial fixture row already
    exists when the worker parks on ``resume`` while still holding the shared
    lock inside ``db.transaction()``. The row count is read back immediately
    through the same reentrant lock (from the worker that owns it), proving
    partial state exists before any competitor can run.

    ``fail_on_resume`` raises after the handshake (force a rollback); otherwise
    the import continues to completion (success/idempotency).
    """

    def __init__(self, db, *, fail_on_resume: bool, client: ESPNClient):
        super().__init__(db, client=client)
        self.fixture_written = threading.Event()
        self.resume = threading.Event()
        # Match counts observed from inside the transaction, after the first
        # fixture write and before the competitor is started.
        self.rows_visible_inside_txn = []
        self._paused = False
        self._fail_on_resume = fail_on_resume

    def _resolve_match(self, m, team_ids, competition_id, season_year):
        match_id = super()._resolve_match(m, team_ids, competition_id, season_year)
        if not self._paused:
            self._paused = True
            # Reentrant lock: this worker still owns the shared lock inside
            # db.transaction(), so it sees the uncommitted row it just wrote.
            self.rows_visible_inside_txn.append(count(self.db, "matches"))
            self.fixture_written.set()
            if not self.resume.wait(HANDSHAKE_TIMEOUT):
                raise RuntimeError("test handshake timed out")
            if self._fail_on_resume:
                raise RuntimeError("forced ESPN failure")
        return match_id


def _exercise_blocking_competitor(svc, cache, key):
    """Drive the export/competitor handshake; return captured worker errors.

    Starts the ESPN export, waits (bounded, for deadlock detection) until the
    first fixture row exists inside the still-open transaction, starts a
    competing cache commit, proves it cannot finish while the shared lock is
    held, then resumes the export and joins both workers. The worker threads are
    ALWAYS resumed and joined in ``finally`` so the connection is never closed
    under a live worker, even on assertion failure.
    """
    exporter_errors = []
    competitor_errors = []
    competitor_started = threading.Event()
    competed = threading.Event()

    def run_export():
        try:
            svc.sync_season("EL", 2024)
        except BaseException as exc:  # noqa: BLE001 - captured for assertion
            exporter_errors.append(exc)

    def run_competitor():
        competitor_started.set()
        try:
            cache.set(key, {"ok": True})
        except BaseException as exc:  # noqa: BLE001 - captured for assertion
            competitor_errors.append(exc)
        else:
            competed.set()

    exporter = threading.Thread(target=run_export, daemon=True)
    competitor = None
    exporter.start()
    try:
        assert svc.fixture_written.wait(HANDSHAKE_TIMEOUT), (
            "ESPN transaction never reached the first-fixture pause point"
        )
        # Partial state already exists inside the still-open transaction.
        assert svc.rows_visible_inside_txn == [1], svc.rows_visible_inside_txn

        competitor = threading.Thread(target=run_competitor, daemon=True)
        competitor.start()
        assert competitor_started.wait(HANDSHAKE_TIMEOUT), (
            "competing worker never started"
        )
        # Give the started competitor a bounded opportunity to reach its write;
        # an immediate is_set() check could pass before cache.set even executes.
        # This observes blocking, not the duration of the full ESPN import.
        assert not competed.wait(0.5), "competing commit ran inside the transaction"

        svc.resume.set()
        exporter.join(HANDSHAKE_TIMEOUT)
        assert not exporter.is_alive(), "ESPN export thread did not finish after resume"
        competitor.join(HANDSHAKE_TIMEOUT)
        assert not competitor.is_alive(), "competing thread did not finish"

        # Only now may the competing operation have finished.
        assert competed.wait(HANDSHAKE_TIMEOUT), (
            "competing commit never completed after the transaction ended"
        )
    finally:
        # ALWAYS unblock and join both workers before the caller closes the DB.
        svc.resume.set()
        exporter.join(HANDSHAKE_TIMEOUT)
        if competitor is not None:
            competitor.join(HANDSHAKE_TIMEOUT)

    return exporter_errors, competitor_errors


def test_competing_commit_waits_while_transaction_open_then_rollback(tmp_path, monkeypatch):
    _pin_current_season(monkeypatch)
    db = Database(db_path=str(tmp_path / "rollback.db"))
    try:
        cache = APICache(db)  # table created/committed before any thread starts
        svc = PausingESPNService(
            db, fail_on_resume=True, client=el_client(2024)
        )

        exporter_errors, competitor_errors = _exercise_blocking_competitor(
            svc, cache, "espn:concurrency"
        )

        assert competitor_errors == [], competitor_errors
        # The forced failure propagated out of the export worker exactly once.
        assert len(exporter_errors) == 1, exporter_errors
        assert isinstance(exporter_errors[0], RuntimeError)

        # Rollback left no partial import (mappings, teams, matches, standings).
        assert count(db, "teams") == 0
        assert count(db, "matches") == 0
        assert count(db, "provider_team_mappings") == 0
        assert count(db, "provider_match_mappings") == 0
        assert count(db, "standings") == 0
        # ...while the competing commit did land after the rollback, so the
        # connection is usable.
        assert cache.get("espn:concurrency") == {"ok": True}
    finally:
        db.close()


def test_successful_transaction_blocks_competitor_then_stays_idempotent(tmp_path, monkeypatch):
    _pin_current_season(monkeypatch)
    db = Database(db_path=str(tmp_path / "success.db"))
    try:
        cache = APICache(db)
        svc = PausingESPNService(
            db, fail_on_resume=False, client=el_client(2024)
        )

        exporter_errors, competitor_errors = _exercise_blocking_competitor(
            svc, cache, "espn:concurrency_ok"
        )

        assert exporter_errors == [], exporter_errors
        assert competitor_errors == [], competitor_errors

        # The successful import committed fully, alongside the competing write.
        assert count(db, "matches", "WHERE competition_id = 'EL'") == 189
        assert count(db, "teams") == 36
        assert count(db, "provider_match_mappings") == 189
        assert count(db, "standings", "WHERE competition_id = 'EL'") == 36
        assert cache.get("espn:concurrency_ok") == {"ok": True}

        # A repeat sync is still idempotent: no duplicates, no new teams.
        counts = svc.sync_season("EL", 2024)
        assert counts["matches"] == 189
        assert counts["teamsCreated"] == 0 and counts["teamsReused"] == 36
        assert counts["matchesNew"] == 0 and counts["matchesUpdated"] == 189
        assert count(db, "matches", "WHERE competition_id = 'EL'") == 189
        assert count(db, "provider_match_mappings") == 189
        assert count(db, "standings", "WHERE competition_id = 'EL'") == 36
    finally:
        db.close()


def test_lock_released_after_failed_transaction(tmp_path, monkeypatch):
    _pin_current_season(monkeypatch)
    db = Database(db_path=str(tmp_path / "error.db"))
    try:
        class Failing(ESPNDataService):
            def _replace_standings(self, *args, **kwargs):
                raise RuntimeError("forced ESPN failure")

        svc = Failing(db, client=el_client(2024))
        with pytest.raises(RuntimeError):
            svc.sync_season("EL", 2024)

        assert count(db, "teams") == 0
        assert count(db, "matches") == 0
        assert count(db, "provider_team_mappings") == 0
        assert count(db, "standings") == 0

        # The lock was released on the error path: an immediate write succeeds.
        db.execute(
            "INSERT INTO api_cache (cache_key, endpoint, response_data, cached_at, "
            "expires_at) VALUES ('k', 'e', '{}', 'now', 'later')"
        )
        db.commit()
        assert db.fetchone(
            "SELECT response_data FROM api_cache WHERE cache_key = 'k'"
        )[0] == "{}"
    finally:
        db.close()
