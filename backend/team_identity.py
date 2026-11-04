"""Shared retired-team redirect resolution for canonical club identity.

Reviewed reconciliation retires duplicate club rows instead of deleting them: a
``team_redirects(retired_team_id, canonical_team_id)`` row records that a stored
id now resolves to a final *active* root. This helper is the single place every
runtime writer consults so a later payload carrying a retired provider id cannot
recreate or reference the retired row.

Contract:

* Accepts either the application :class:`~backend.database.Database` wrapper or a
  native DuckDB connection (the offline historical-repair entry point passes the
  latter).
* On a legacy schema without ``team_redirects`` it is a pure identity function:
  resolution returns the original id and nothing is created or mutated.
* When the table exists it requires every target to be a real, final active root.
  Self-redirects, dangling ids, chains and cycles raise :class:`TeamIdentityError`
  rather than being guessed at.

There is deliberately no fuzzy/name-based guessing here; canonicalization is a
pure id -> id lookup over reviewed facts.
"""
from __future__ import annotations

from typing import Dict, Iterable, List, Optional, Set

import duckdb

from backend.database import Database

_REDIRECT_TABLE = "team_redirects"


class TeamIdentityError(RuntimeError):
    """A retired-team redirect could not be resolved safely (corrupt/existing)."""


class TeamRedirectResolver:
    """Resolve stored team ids to their final active canonical root.

    The resolver is cheap and read-only. Construction validates the redirect
    table when present, so a corrupt relationship fails before any writer runs.
    An unsupported or closed source fails loudly instead of silently acting as
    identity.
    """

    def __init__(self, source):
        self._source = source
        run = self._query_interface(source)
        if run is None:
            raise TeamIdentityError(
                f"unsupported team-identity source {type(source).__name__!r}: "
                "expected a Database wrapper or a native DuckDB connection"
            )
        self._query = run
        self._redirects: Dict[int, int] = {}
        self._load()

    @staticmethod
    def _query_interface(source):
        """Return ``run(query, params) -> list`` for a supported source.

        A native DuckDB connection has its own ``execute`` shape; a wrapper (the
        application ``Database`` or a duck-typed equivalent) exposes a real
        ``conn`` and ``fetchall(query, params)``. A closed wrapper raises here and
        any other object is unsupported (``None``); the constructor fails closed
        rather than pretending to be identity.
        """
        if isinstance(source, duckdb.DuckDBPyConnection):
            def run(query, params=None):
                cursor = source.execute(query, params) if params else source.execute(query)
                return cursor.fetchall()
            return run
        conn = getattr(source, "conn", None)
        if isinstance(conn, duckdb.DuckDBPyConnection):
            def run(query, params=None):
                return source.fetchall(query, params) if params else source.fetchall(query)
            return run
        if isinstance(source, Database):
            raise TeamIdentityError(
                "Database connection is closed; cannot resolve team identity"
            )
        return None

    def _load(self) -> None:
        # Presence is determined by an actual row count. A real query failure
        # (aborted/closed/transient) propagates instead of being mistaken for an
        # absent table, so a broken source never silently resolves to identity.
        present = bool(self._query(
            "SELECT 1 FROM information_schema.tables WHERE table_name = ?",
            (_REDIRECT_TABLE,),
        ))
        if not present:
            return

        redirects: Dict[int, int] = {}
        for retired, canonical in self._query(
            "SELECT retired_team_id, canonical_team_id FROM team_redirects"
        ):
            retired, canonical = int(retired), int(canonical)
            if retired == canonical:
                raise TeamIdentityError(
                    f"team redirect {retired} points at itself"
                )
            redirects[retired] = canonical

        team_ids = {int(row[0]) for row in self._query("SELECT id FROM teams")}
        for retired, canonical in redirects.items():
            if retired not in team_ids:
                raise TeamIdentityError(
                    f"team redirect {retired} -> {canonical} has no teams row for "
                    f"the retired id"
                )
            if canonical not in team_ids:
                raise TeamIdentityError(
                    f"team redirect {retired} -> {canonical} targets a missing team"
                )
        for retired, canonical in redirects.items():
            if canonical in redirects:
                raise TeamIdentityError(
                    f"team redirect {retired} -> {canonical} is not a final active "
                    f"root (redirect chain or cycle)"
                )

        self._redirects = redirects

    def canonical_id(self, team_id: Optional[int]) -> Optional[int]:
        """Return the final active root for ``team_id`` (identity when unmapped)."""
        if team_id is None:
            return None
        tid = int(team_id)
        return self._redirects.get(tid, tid)

    def canonical_ids(self, team_ids: Iterable[int]) -> List[int]:
        """Canonicalize a sequence of ids, preserving order."""
        return [self.canonical_id(tid) for tid in team_ids]

    def retired_ids(self) -> Set[int]:
        """Every reviewed retired id in this source."""
        return set(self._redirects)

    def is_retired(self, team_id: Optional[int]) -> bool:
        """True when ``team_id`` is a reviewed retired identity."""
        return team_id is not None and int(team_id) in self._redirects
