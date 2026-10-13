"""Regression tests for knockout stage scoping in ``PlayoffAnalyzer``.

Discovered bug (stage endpoint leak): the public
``PlayoffAnalyzer.get_pairs_by_stage(stage)`` query leaked pairs across stages.

* ``FINAL`` used the round pattern ``%FINAL%``, which also matches
  ``Quarter-final``/``Semi-final`` because both contain the substring "final".
* ``ROUND_OF_16`` had a matchday fallback of 9..10 that swept up rows which
  explicitly declared the semi-final (md 9) or final (md 10) round.
* A row whose ``stage`` column named a stage (``QUARTER_FINAL`` etc.) but whose
  ``round`` was NULL was only reachable through an ambiguous matchday fallback,
  so it either disappeared or landed in a sibling tab.

These tests pin every stage query to its own pairs only, keep the matchday
fallback for genuinely untyped rows (NULL/blank round with a generic knockout
or NULL stage), and assert league-phase matchdays 1..8 never surface.

Everything runs against a real temporary DuckDB. ``PlayoffAnalyzer`` is built
with the historical 2024 constructor so no present-day Elo/live data is read;
the API client is a no-network stub and the resolved current season is pinned so
the historical branch is deterministic.
"""
from contextlib import contextmanager
from unittest.mock import patch

import pytest

from backend.database import Database
from backend.playoff_analyzer import PlayoffAnalyzer

STAGES = ["KNOCKOUT_PLAYOFF", "ROUND_OF_16", "QUARTER_FINAL", "SEMI_FINAL", "FINAL"]

# Each stage gets a distinct team pair so a pair collapsed into the wrong tab
# cannot be hidden by another stage sharing teams.
R16 = (1, 2)
QF = (3, 4)
SF = (5, 6)
FINAL = (7, 8)


class MockAPI:
    """No-network stand-in for ``APIClient``.

    ``get_pairs_by_stage`` must never need the API for a historical season;
    any attribute access is recorded and raises instead of doing I/O.
    """

    def __init__(self):
        object.__setattr__(self, "calls", [])

    def __getattr__(self, name):
        def _explode(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            raise AssertionError(f"unexpected API access: {name}")

        return _explode


@contextmanager
def historical_current(year=2026):
    """Pin the resolved current season so 2024/2025 are treated as historical."""
    with patch("backend.season_service.current_season_year", lambda today=None: year):
        yield


@pytest.fixture
def db(tmp_path):
    database = Database(db_path=str(tmp_path / "knockout_stage_scope.db"))
    yield database
    database.close()


# ---------------------------------------------------------------------------
# insertion helpers
# ---------------------------------------------------------------------------

def add_team(db, team_id, name):
    db.execute(
        "INSERT INTO teams (id, name, code, crest) VALUES (?, ?, ?, ?)",
        (team_id, name, f"T{team_id}", None),
    )


def add_match(
    db,
    match_id,
    home,
    away,
    date,
    *,
    stage="KNOCKOUT",
    round_name=None,
    matchday=None,
    group_name=None,
    competition_id="CL",
    status="FINISHED",
):
    db.execute(
        """
        INSERT INTO matches (
            id, home_team_id, away_team_id, home_score, away_score,
            matchday, "date", status, stage, "round", group_name, competition_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (match_id, home, away, 1, 0, matchday, date, status, stage, round_name,
         group_name, competition_id),
    )


def add_pair(db, first_id, first_team, second_team, date, **kwargs):
    add_team(db, first_team, f"Team {first_team}")
    add_team(db, second_team, f"Team {second_team}")
    add_match(db, first_id, first_team, second_team, date, **kwargs)


def pairs(db, stage, *, competition_id="CL", season_start_year=2024, api=None):
    """Run the public stage query for a pinned historical season."""
    with historical_current():
        analyzer = PlayoffAnalyzer(
            db,
            api if api is not None else MockAPI(),
            competition_id=competition_id,
            season_start_year=season_start_year,
        )
        return analyzer.get_pairs_by_stage(stage, competition_id)


def ids(result):
    return {frozenset((p["team1"]["id"], p["team2"]["id"])) for p in result}


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

def build_explicit_rounds(db):
    """One explicit knockout round per stage, matchday 7..10."""
    add_pair(db, 101, *R16, "2025-03-04T20:00:00Z", round_name="Round of 16", matchday=7)
    add_pair(db, 102, *QF, "2025-04-08T20:00:00Z", round_name="Quarter-finals", matchday=8)
    add_pair(db, 103, *SF, "2025-04-29T20:00:00Z", round_name="Semi-finals", matchday=9)
    add_pair(db, 104, *FINAL, "2025-05-31T20:00:00Z", round_name="Final", matchday=10)
    db.commit()


NAMED = {
    "ROUND_OF_16": (11, 12),
    "QUARTER_FINAL": (13, 14),
    "SEMI_FINAL": (15, 16),
    "FINAL": (17, 18),
}
# matchday deliberately overlaps a sibling stage's fallback range
NAMED_MATCHDAY = {
    "ROUND_OF_16": 10,    # inside ROUND_OF_16 + KNOCKOUT_PLAYOFF fallbacks
    "QUARTER_FINAL": 9,   # inside ROUND_OF_16 + KNOCKOUT_PLAYOFF fallbacks
    "SEMI_FINAL": 10,     # inside ROUND_OF_16 + KNOCKOUT_PLAYOFF fallbacks
    "FINAL": 7,           # inside KNOCKOUT_PLAYOFF fallback
}


def build_named_stage_rows(db):
    """Named API stages with a NULL round and overlapping matchdays."""
    add_pair(db, 201, *NAMED["ROUND_OF_16"], "2025-03-04T20:00:00Z",
             stage="ROUND_OF_16", round_name=None, matchday=NAMED_MATCHDAY["ROUND_OF_16"])
    add_pair(db, 202, *NAMED["QUARTER_FINAL"], "2025-04-08T20:00:00Z",
             stage="QUARTER_FINAL", round_name=None, matchday=NAMED_MATCHDAY["QUARTER_FINAL"])
    add_pair(db, 203, *NAMED["SEMI_FINAL"], "2025-04-29T20:00:00Z",
             stage="SEMI_FINAL", round_name=None, matchday=NAMED_MATCHDAY["SEMI_FINAL"])
    add_pair(db, 204, *NAMED["FINAL"], "2025-05-31T20:00:00Z",
             stage="FINAL", round_name=None, matchday=NAMED_MATCHDAY["FINAL"])
    db.commit()


def add_untyped_row(db, match_id, home, away, date, **kwargs):
    add_team(db, home, f"Team {home}")
    add_team(db, away, f"Team {away}")
    add_match(db, match_id, home, away, date, **kwargs)


# ---------------------------------------------------------------------------
# explicit rounds: each stage query returns only its own pair
# ---------------------------------------------------------------------------

def test_final_stage_does_not_match_quarter_or_semi_rounds(db):
    build_explicit_rounds(db)

    result = ids(pairs(db, "FINAL"))

    assert result == {frozenset(FINAL)}
    assert frozenset(QF) not in result, "Quarter-final leaked into FINAL"
    assert frozenset(SF) not in result, "Semi-final leaked into FINAL"


def test_round_of_16_fallback_does_not_catch_semi_or_final(db):
    build_explicit_rounds(db)

    result = ids(pairs(db, "ROUND_OF_16"))

    assert result == {frozenset(R16)}
    assert frozenset(SF) not in result, "semi-final (md 9) leaked into ROUND_OF_16"
    assert frozenset(FINAL) not in result, "final (md 10) leaked into ROUND_OF_16"


def test_quarter_and_semi_stages_return_only_own_pairs(db):
    build_explicit_rounds(db)

    assert ids(pairs(db, "QUARTER_FINAL")) == {frozenset(QF)}
    assert ids(pairs(db, "SEMI_FINAL")) == {frozenset(SF)}


def test_knockout_playoff_stage_excludes_explicit_rounds(db):
    build_explicit_rounds(db)

    assert pairs(db, "KNOCKOUT_PLAYOFF") == []


def test_final_round_case_and_whitespace(db):
    """Mixed case / surrounding whitespace must still isolate the Final."""
    add_pair(db, 301, 21, 22, "2025-04-08T20:00:00Z",
             round_name="Quarter-Final", matchday=8)
    add_pair(db, 302, 23, 24, "2025-04-29T20:00:00Z",
             round_name="semi-final", matchday=9)
    add_pair(db, 303, 25, 26, "2025-05-31T20:00:00Z",
             round_name="  fInAl  ", matchday=10)
    db.commit()

    result = ids(pairs(db, "FINAL"))

    assert result == {frozenset({25, 26})}
    assert frozenset({21, 22}) not in result
    assert frozenset({23, 24}) not in result


# ---------------------------------------------------------------------------
# named API stages (NULL round, overlapping matchday)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("declared", list(NAMED))
def test_named_stage_classifies_only_under_declared_stage(db, declared):
    build_named_stage_rows(db)
    expected = frozenset(NAMED[declared])

    assert ids(pairs(db, declared)) == {expected}

    for other in STAGES:
        if other == declared:
            continue
        assert expected not in ids(pairs(db, other)), (
            f"{declared} pair leaked into {other}"
        )


# ---------------------------------------------------------------------------
# fallback retained for genuinely untyped rows
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("stage", ["KNOCKOUT", None])
def test_untyped_null_round_uses_matchday_fallback(db, stage):
    add_untyped_row(db, 401, 31, 32, "2025-04-08T20:00:00Z",
                    stage=stage, round_name=None, matchday=11)
    db.commit()
    expected = frozenset({31, 32})

    assert ids(pairs(db, "QUARTER_FINAL")) == {expected}

    for other in ("KNOCKOUT_PLAYOFF", "ROUND_OF_16", "SEMI_FINAL", "FINAL"):
        assert expected not in ids(pairs(db, other)), (
            f"untyped fallback leaked into {other}"
        )


def test_legacy_untyped_playoff_tie_retained(db):
    """Fully NULL-tagged rows are still placed by the February boundary."""
    add_untyped_row(db, 501, 41, 42, "2025-02-11T20:00:00Z",
                    stage=None, round_name=None, matchday=None)
    db.commit()
    expected = frozenset({41, 42})

    assert ids(pairs(db, "KNOCKOUT_PLAYOFF")) == {expected}
    assert ids(pairs(db, "ROUND_OF_16")) == set()


# ---------------------------------------------------------------------------
# no league-phase or cross-competition / cross-season leakage
# ---------------------------------------------------------------------------

def test_league_matchdays_never_surface_in_any_stage(db):
    for offset, matchday in enumerate(range(1, 9)):
        add_untyped_row(db, 601 + offset, 51 + offset * 2, 52 + offset * 2,
                        f"2024-{9 + offset:02d}-15T20:00:00Z",
                        stage="LEAGUE_STAGE", round_name=None, matchday=matchday)
    db.commit()

    for stage in STAGES:
        assert pairs(db, stage) == [], f"league matchdays leaked into {stage}"


def test_other_competition_knockout_does_not_leak(db):
    add_pair(db, 701, *FINAL, "2025-05-31T20:00:00Z",
             round_name="Final", matchday=10, competition_id="CL")
    add_pair(db, 702, 61, 62, "2025-05-31T20:00:00Z",
             round_name="Final", matchday=10, competition_id="EL")
    db.commit()

    assert ids(pairs(db, "FINAL", competition_id="CL")) == {frozenset(FINAL)}


def test_historical_seasons_are_isolated(db):
    add_pair(db, 801, *FINAL, "2025-05-31T20:00:00Z",
             round_name="Final", matchday=10)
    add_pair(db, 802, 71, 72, "2026-05-31T20:00:00Z",
             round_name="Final", matchday=10)
    db.commit()

    assert ids(pairs(db, "FINAL", season_start_year=2024)) == {frozenset(FINAL)}
    assert ids(pairs(db, "FINAL", season_start_year=2025)) == {frozenset({71, 72})}
