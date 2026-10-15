"""Regression tests for stored knockout leg results and aggregates.

Design contract (docs/superpowers/plans/2026-10-15-knockout-results.md):

* Every pair gains ``legs``: a **chronological** list of fixtures, each with
  ``matchId``, ``homeTeamId``, ``awayTeamId``, ``homeScore``, ``awayScore``,
  ``status`` and ``date``. Missing scores stay ``None`` — never turned into 0.
* Every pair gains ``aggregateScore``: ``{team1: int, team2: int}`` **only**
  for a completed two-leg tie whose two legs are both ``FINISHED`` with both
  scores available; otherwise ``None``. Scores are oriented by the pair's
  stable team1/team2 identity, not by the latest leg's home/away order.
* A single-match final exposes its own result, not an aggregate. Drawn
  aggregates never imply a winner (stored goals cannot encode shootouts).
* Both pair-building paths (``get_playoff_pairs`` and ``get_pairs_by_stage``)
  expose the same contract, scoped to the selected season.

Everything runs against a real temporary DuckDB. ``PlayoffAnalyzer`` is built
with a historical constructor so no present-day Elo/live data is read; the API
client is a no-network stub and the resolved current season is pinned so the
historical branch is deterministic.
"""
from contextlib import contextmanager
from unittest.mock import patch

import pytest

from backend.database import Database
from backend.playoff_analyzer import PlayoffAnalyzer

REQUIRED_LEG_KEYS = {
    "matchId",
    "homeTeamId",
    "awayTeamId",
    "homeScore",
    "awayScore",
    "status",
    "date",
}


class MockAPI:
    """No-network stand-in for ``APIClient``: any access raises."""

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
    database = Database(db_path=str(tmp_path / "knockout_results.db"))
    yield database
    database.close()


# ---------------------------------------------------------------------------
# insertion helpers
# ---------------------------------------------------------------------------

def add_team(db, team_id, name):
    db.execute(
        "INSERT INTO teams (id, name, code, crest) VALUES (?, ?, ?, ?)",
        (team_id, name, f"T{team_id}", f"https://crest.example/{team_id}.png"),
    )


def add_match(
    db,
    match_id,
    home,
    away,
    date,
    *,
    home_score=None,
    away_score=None,
    status="FINISHED",
    stage="KNOCKOUT",
    round_name=None,
    matchday=None,
    group_name=None,
    competition_id="CL",
):
    db.execute(
        """
        INSERT INTO matches (
            id, home_team_id, away_team_id, home_score, away_score,
            matchday, "date", status, stage, "round", group_name, competition_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (match_id, home, away, home_score, away_score, matchday, date, status,
         stage, round_name, group_name, competition_id),
    )


def add_leg(db, match_id, home, away, date, home_team, away_team, **kwargs):
    """Insert a fixture, creating both teams if they do not exist yet."""
    for team_id, name in ((home, home_team), (away, away_team)):
        if not db.fetchone("SELECT 1 FROM teams WHERE id = ?", (team_id,)):
            add_team(db, team_id, name)
    add_match(db, match_id, home, away, date, **kwargs)


def by_stage(db, stage, *, season_start_year=2024, competition_id="CL", api=None):
    with historical_current():
        analyzer = PlayoffAnalyzer(
            db,
            api if api is not None else MockAPI(),
            competition_id=competition_id,
            season_start_year=season_start_year,
        )
        return analyzer.get_pairs_by_stage(stage, competition_id)


def all_pairs(db, *, season_start_year=2024, competition_id="CL", api=None):
    with historical_current():
        analyzer = PlayoffAnalyzer(
            db,
            api if api is not None else MockAPI(),
            competition_id=competition_id,
            season_start_year=season_start_year,
        )
        return analyzer.get_playoff_pairs(competition_id)


def pair_for(pairs, team_a, team_b):
    wanted = frozenset((team_a, team_b))
    matches = [p for p in pairs if frozenset((p["team1"]["id"], p["team2"]["id"])) == wanted]
    assert len(matches) == 1, f"expected exactly one pair for {wanted}, got {len(matches)}"
    return matches[0]


def assert_leg_shape(leg):
    assert REQUIRED_LEG_KEYS <= set(leg), sorted(REQUIRED_LEG_KEYS - set(leg))


# ---------------------------------------------------------------------------
# two-leg tie: chronological legs, stable orientation, aggregate
# ---------------------------------------------------------------------------

def test_two_leg_tie_exposes_chronological_legs_and_aggregate(db):
    # Insert the later leg first to prove ordering is by date, not insertion.
    add_leg(db, 102, 2, 1, "2025-03-11T20:00:00Z", "Bravo", "Alpha",
            home_score=1, away_score=3, round_name="Round of 16")
    add_leg(db, 101, 1, 2, "2025-03-04T20:00:00Z", "Alpha", "Bravo",
            home_score=2, away_score=0, round_name="Round of 16")
    db.commit()

    for pair in (by_stage(db, "ROUND_OF_16")[0], pair_for(all_pairs(db), 1, 2)):
        # team1/team2 identity is the earliest leg's home/away and never flips.
        assert pair["team1"]["id"] == 1
        assert pair["team2"]["id"] == 2

        legs = pair["legs"]
        assert [leg["matchId"] for leg in legs] == [101, 102]
        for leg in legs:
            assert_leg_shape(leg)

        leg1, leg2 = legs
        assert leg1 == {
            "matchId": 101,
            "homeTeamId": 1,
            "awayTeamId": 2,
            "homeScore": 2,
            "awayScore": 0,
            "status": "FINISHED",
            "date": "2025-03-04T20:00:00Z",
        }
        assert leg2 == {
            "matchId": 102,
            "homeTeamId": 2,
            "awayTeamId": 1,
            "homeScore": 1,
            "awayScore": 3,
            "status": "FINISHED",
            "date": "2025-03-11T20:00:00Z",
        }

        # Orientation by pair identity: team1 = Alpha = 2 + 3 = 5; team2 = 1.
        assert pair["aggregateScore"] == {"team1": 5, "team2": 1}


def test_aggregate_orients_reversed_home_away_to_stable_teams(db):
    # Both legs away-heavy for team1: proves scores follow team identity.
    add_leg(db, 111, 2, 1, "2025-03-04T20:00:00Z", "Bravo", "Alpha",
            home_score=1, away_score=1, round_name="Round of 16")
    add_leg(db, 112, 2, 1, "2025-03-11T20:00:00Z", "Bravo", "Alpha",
            home_score=0, away_score=2, round_name="Round of 16")
    db.commit()

    pair = by_stage(db, "ROUND_OF_16")[0]

    assert pair["team1"]["id"] == 2 and pair["team2"]["id"] == 1
    # team1 = Bravo: 1 (leg 1) + 0 (leg 2) = 1; team2 = Alpha: 1 + 2 = 3.
    assert pair["aggregateScore"] == {"team1": 1, "team2": 3}


# ---------------------------------------------------------------------------
# single-match final: own result, never an aggregate
# ---------------------------------------------------------------------------

def test_single_match_final_has_result_not_aggregate(db):
    add_leg(db, 301, 7, 8, "2025-05-31T20:00:00Z", "Zeta", "Omega",
            home_score=2, away_score=1, round_name="Final")
    db.commit()

    stage_pairs = by_stage(db, "FINAL")
    assert len(stage_pairs) == 1

    for pair in (stage_pairs[0], pair_for(all_pairs(db), 7, 8)):
        assert pair["aggregateScore"] is None
        legs = pair["legs"]
        assert len(legs) == 1
        assert_leg_shape(legs[0])
        assert legs[0]["matchId"] == 301
        assert legs[0]["homeTeamId"] == 7
        assert legs[0]["awayTeamId"] == 8
        assert legs[0]["homeScore"] == 2
        assert legs[0]["awayScore"] == 1
        assert legs[0]["status"] == "FINISHED"


# ---------------------------------------------------------------------------
# missing / null scores are never invented as zero
# ---------------------------------------------------------------------------

def test_finished_legs_without_scores_keep_nulls_and_no_aggregate(db):
    add_leg(db, 401, 3, 4, "2025-03-04T20:00:00Z", "Charlie", "Delta",
            home_score=None, away_score=None, round_name="Round of 16")
    add_leg(db, 402, 4, 3, "2025-03-11T20:00:00Z", "Delta", "Charlie",
            home_score=1, away_score=None, round_name="Round of 16")
    db.commit()

    pair = by_stage(db, "ROUND_OF_16")[0]

    assert pair["aggregateScore"] is None
    legs = {leg["matchId"]: leg for leg in pair["legs"]}
    assert legs[401]["homeScore"] is None and legs[401]["awayScore"] is None
    assert legs[402]["homeScore"] == 1 and legs[402]["awayScore"] is None
    # A missing value must never be silently coerced to 0.
    assert legs[401]["homeScore"] != 0 and legs[401]["awayScore"] != 0


def test_partial_scheduled_tie_has_no_aggregate(db):
    add_leg(db, 501, 5, 6, "2025-03-04T20:00:00Z", "Echo", "Foxtrot",
            home_score=1, away_score=0, round_name="Round of 16")
    add_leg(db, 502, 6, 5, "2025-03-11T20:00:00Z", "Foxtrot", "Echo",
            home_score=None, away_score=None, status="SCHEDULED",
            round_name="Round of 16")
    db.commit()

    pair = by_stage(db, "ROUND_OF_16")[0]

    assert pair["aggregateScore"] is None
    legs = pair["legs"]
    assert [leg["status"] for leg in legs] == ["FINISHED", "SCHEDULED"]
    assert legs[1]["homeScore"] is None and legs[1]["awayScore"] is None


def test_zero_zero_completed_tie_aggregates_to_zero_not_null(db):
    add_leg(db, 601, 11, 12, "2025-03-04T20:00:00Z", "Golf", "Hotel",
            home_score=0, away_score=0, round_name="Round of 16")
    add_leg(db, 602, 12, 11, "2025-03-11T20:00:00Z", "Hotel", "Golf",
            home_score=0, away_score=0, round_name="Round of 16")
    db.commit()

    pair = by_stage(db, "ROUND_OF_16")[0]

    assert pair["aggregateScore"] == {"team1": 0, "team2": 0}
    assert pair["aggregateScore"] is not None


# ---------------------------------------------------------------------------
# drawn aggregate must not imply a winner
# ---------------------------------------------------------------------------

def test_drawn_aggregate_does_not_infer_winner_or_shootout(db):
    add_leg(db, 701, 13, 14, "2025-03-04T20:00:00Z", "India", "Juliet",
            home_score=1, away_score=1, round_name="Round of 16")
    add_leg(db, 702, 14, 13, "2025-03-11T20:00:00Z", "Juliet", "India",
            home_score=2, away_score=2, round_name="Round of 16")
    db.commit()

    pair = by_stage(db, "ROUND_OF_16")[0]

    # team1 = India: 1 + 2 = 3; team2 = Juliet: 1 + 2 = 3 -> level tie.
    assert pair["aggregateScore"] == {"team1": 3, "team2": 3}
    # The payload must not designate any winner: goals alone cannot encode one.
    for key in ("winner", "winnerTeamId", "advanced", "penaltyShootout",
                "penalties", "aggWinner"):
        assert key not in pair, f"unexpected inferred field: {key}"


# ---------------------------------------------------------------------------
# season scoping
# ---------------------------------------------------------------------------

def _two_season_ties(db):
    # 2024/25 tie: Alpha 2-0 Bravo, then Bravo 1-3 Alpha -> aggregate 5-1.
    add_leg(db, 101, 1, 2, "2025-03-04T20:00:00Z", "Alpha", "Bravo",
            home_score=2, away_score=0, round_name="Round of 16")
    add_leg(db, 102, 2, 1, "2025-03-11T20:00:00Z", "Bravo", "Alpha",
            home_score=1, away_score=3, round_name="Round of 16")
    # 2025/26 tie for the same teams: Alpha 4-1 Bravo, then Bravo 0-0 Alpha.
    add_leg(db, 201, 1, 2, "2026-03-04T20:00:00Z", "Alpha", "Bravo",
            home_score=4, away_score=1, round_name="Round of 16")
    add_leg(db, 202, 2, 1, "2026-03-11T20:00:00Z", "Bravo", "Alpha",
            home_score=0, away_score=0, round_name="Round of 16")
    db.commit()


def test_season_scoping_isolates_legs_and_aggregates(db):
    _two_season_ties(db)

    pair_2024 = by_stage(db, "ROUND_OF_16", season_start_year=2024)[0]
    assert [leg["matchId"] for leg in pair_2024["legs"]] == [101, 102]
    assert pair_2024["aggregateScore"] == {"team1": 5, "team2": 1}

    pair_2025 = by_stage(db, "ROUND_OF_16", season_start_year=2025)[0]
    assert [leg["matchId"] for leg in pair_2025["legs"]] == [201, 202]
    # team1 = Alpha: 4 + 0 = 4; team2 = Bravo: 1 + 0 = 1.
    assert pair_2025["aggregateScore"] == {"team1": 4, "team2": 1}

    # Same isolation on the all-stages path.
    all_2024 = pair_for(all_pairs(db, season_start_year=2024), 1, 2)
    assert [leg["matchId"] for leg in all_2024["legs"]] == [101, 102]
    assert all_2024["aggregateScore"] == {"team1": 5, "team2": 1}

    all_2025 = pair_for(all_pairs(db, season_start_year=2025), 1, 2)
    assert [leg["matchId"] for leg in all_2025["legs"]] == [201, 202]
    assert all_2025["aggregateScore"] == {"team1": 4, "team2": 1}


def test_distinct_rounds_for_same_pair_do_not_fabricate_aggregate(db):
    # Synthetic but real: the same teams meet in the Round of 16 and again in
    # the Final. The all-stages view retains one determined stage for the pair,
    # so fixtures from the other round must never be summed into an aggregate.
    add_leg(db, 901, 1, 2, "2025-03-04T20:00:00Z", "Alpha", "Bravo",
            home_score=1, away_score=0, round_name="Round of 16")
    add_leg(db, 902, 1, 2, "2025-05-31T20:00:00Z", "Alpha", "Bravo",
            home_score=2, away_score=1, round_name="Final")
    db.commit()

    pair = pair_for(all_pairs(db, season_start_year=2024), 1, 2)

    # Two finished scored fixtures exist for the pair, but they belong to two
    # different rounds: only the retained stage's fixture is attached.
    assert pair["stage"] == "Round of 16"
    assert [leg["matchId"] for leg in pair["legs"]] == [901]
    assert pair["aggregateScore"] is None


def test_archived_pair_keeps_probability_unavailable_with_results(db):
    add_leg(db, 801, 1, 2, "2025-03-04T20:00:00Z", "Alpha", "Bravo",
            home_score=2, away_score=0, round_name="Round of 16")
    add_leg(db, 802, 2, 1, "2025-03-11T20:00:00Z", "Bravo", "Alpha",
            home_score=1, away_score=3, round_name="Round of 16")
    db.commit()

    pair = by_stage(db, "ROUND_OF_16", season_start_year=2024)[0]

    # Results are exposed for the archive, but probabilities stay unavailable.
    assert pair["aggregateScore"] == {"team1": 5, "team2": 1}
    assert pair["winProbability"]["method"] == "historical_no_data"
    assert pair["winProbability"]["team1Win"] == 0.0
    assert pair["winProbability"]["team2Win"] == 0.0
