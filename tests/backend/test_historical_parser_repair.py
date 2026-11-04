"""Regression tests for historical openfootball parser + team matcher repair.

Repairs under test (public API only, no internals beyond matcher normalize):
- kickoff with colon HH:MM no longer retained inside team names (dot HH.MM already stripped)
- date lookup uses the NEAREST preceding date header, not the oldest one in the
  lookback window, and never loses the date inside a large (18-match) date block
- season rollover: Jan/Feb dates resolve to season_year + 1
- sequential parses on one parser instance are fully reset (no stage/date leakage)
- new-format "League, Matchday N" and "Playoffs, Matchday N" headers emit an
  explicit matchday field plus LEAGUE_STAGE/KNOCKOUT_PLAYOFF stage tags
- R16/QF/SF/Final headers map to real rounds with real scores
- penalty/shootout and a.e.t. annotations: use the underlying match score, not
  the shootout score
- TeamMatcher strips kickoff prefixes before lookup/store so time-prefixed
  variants resolve to the SAME existing club and no time artifacts are stored
  for newly created teams
"""
import pytest

from backend.database import Database
from backend.football_txt_parser import FootballTxtParser
from backend.team_matcher import TeamMatcher


# ---------------------------------------------------------------------------
# fixtures / helpers
# ---------------------------------------------------------------------------

@pytest.fixture
def db(tmp_path):
    database = Database(db_path=str(tmp_path / "matcher_repair.db"))
    yield database
    database.close()


def add_team(db, team_id, name):
    db.execute(
        "INSERT INTO teams (id, name, code, crest) VALUES (?, ?, ?, ?)",
        (team_id, name, None, None),
    )


def parse(content, season="2024-25"):
    """Parse content with the default CL / season used by historical import."""
    return FootballTxtParser().parse(content, competition="CL", season=season)


# ---------------------------------------------------------------------------
# kickoff stripping: colon vs dot
# ---------------------------------------------------------------------------

def test_colon_kickoff_not_retained_in_team_name():
    content = (
        "= UEFA Champions League 2024/25\n"
        "▪ League, Matchday 1\n"
        "  Tue Sep 17 2024\n"
        "    18:45  Team A (ENG)  v Team B (ITA)  0-3 (0-2)\n"
    )
    parsed = parse(content)
    match = parsed["matches"][0]

    # The repaired parser must strip the colon kickoff from the name, exactly
    # like the dot variant ("18:45 Team A" is the current artifact).
    assert match["home_team"] == "Team A"
    assert match["away_team"] == "Team B"
    assert "18:45" not in match["home_team"]
    assert "18:45" not in match["away_team"]


def test_dot_kickoff_stripped_from_team_name():
    content = (
        "= UEFA Champions League 2024/25\n"
        "▪ League, Matchday 1\n"
        "  Tue Sep 17 2024\n"
        "    20.45  Team A (ENG)  v Team B (ITA)  1-1\n"
    )
    parsed = parse(content)
    match = parsed["matches"][0]

    assert match["home_team"] == "Team A"
    assert match["away_team"] == "Team B"


def test_line_without_kickoff_inherits_date():
    content = (
        "= UEFA Champions League 2024/25\n"
        "▪ League, Matchday 1\n"
        "  Tue Sep 17 2024\n"
        "    Team C (FRA)  v Team D (GER)  2-1\n"
    )
    parsed = parse(content)
    match = parsed["matches"][0]

    assert match["date"] == "2024-09-17"
    assert match["home_team"] == "Team C"
    assert match["away_team"] == "Team D"


# ---------------------------------------------------------------------------
# date header resolution
# ---------------------------------------------------------------------------

def test_nearest_date_header_wins_over_older_one():
    content = (
        "= UEFA Champions League 2024/25\n"
        "▪ League, Matchday 1\n"
        "  Tue Sep 17 2024\n"
        "    18:45  Team A (ENG)  v Team B (ITA)  0-3\n"
        "▪ League, Matchday 2\n"
        "  Wed Sep 25 2024\n"
        "    20.00  Team C (ESP)  v Team D (GER)  1-1\n"
    )
    parsed = parse(content)

    assert parsed["matches"][0]["date"] == "2024-09-17"
    # Must use the nearest preceding header, not the oldest one in the window.
    assert parsed["matches"][1]["date"] == "2024-09-25"


def test_eighteen_match_date_block_keeps_actual_date():
    lines = ["= UEFA Champions League 2024/25\n",
             "▪ League, Matchday 1\n",
             "  Tue Sep 17 2024\n"]
    for i in range(18):
        lines.append(f"    18:45  Team {i:02d} (ENG)  v Team {i + 36:02d} (ITA)  0-1\n")
    parsed = parse("".join(lines))

    assert len(parsed["matches"]) == 18
    # Every match in the block keeps the real date; lookback must not truncate
    # matches more than 10 lines after the header.
    for match in parsed["matches"]:
        assert match["date"] == "2024-09-17"


def test_january_date_rolls_over_to_season_year_plus_one():
    content = (
        "= UEFA Champions League 2024/25\n"
        "▪ League, Matchday 8\n"
        "  Tue Jan 29 2025\n"
        "    21:00  Team A (ENG)  v Team B (ITA)  3-0\n"
    )
    parsed = parse(content)

    assert parsed["matches"][0]["date"] == "2025-01-29"


def test_sequential_parse_resets_state_no_leakage():
    parser = FootballTxtParser()
    first = (
        "= UEFA Champions League 2024/25\n"
        "▪ Finals, Round of 16\n"
        "  Tue Mar 4\n"
        "    21:00  Team A (ENG)  v Team B (ITA)  2-1\n"
    )
    second = (
        "= UEFA Champions League 2024/25\n"
        "▪ League, Matchday 1\n"
        "  Tue Sep 17 2024\n"
        "    18:45  Team C (ESP)  v Team D (GER)  1-0\n"
    )
    parsed1 = parser.parse(first, competition="CL", season="2024-25")
    parsed2 = parser.parse(second, competition="CL", season="2024-25")

    assert parsed1["matches"][0]["stage"] == "KNOCKOUT"
    # Second parse must not inherit the knockout round or the March date.
    assert parsed2["matches"][0]["stage"] == "LEAGUE_STAGE"
    assert parsed2["matches"][0]["round"] is None
    assert parsed2["matches"][0]["date"] == "2024-09-17"


# ---------------------------------------------------------------------------
# new-format headers: matchday field + stage tags
# ---------------------------------------------------------------------------

def test_league_matchday_field_1_to_8():
    lines = ["= UEFA Champions League 2024/25\n"]
    for md in range(1, 9):
        lines.append(f"▪ League, Matchday {md}\n")
        lines.append(f"  Tue Sep {10 + md} 2024\n")
        lines.append(f"    18:45  Team {md:02d} (ENG)  v Team {md + 10:02d} (ITA)  1-0\n")
    parsed = parse("".join(lines))

    assert len(parsed["matches"]) == 8
    for i, match in enumerate(parsed["matches"], start=1):
        assert match["stage"] == "LEAGUE_STAGE"
        # New contract: explicit matchday needed to reconstruct real rounds.
        assert match["matchday"] == i


def test_playoffs_matchday_1_and_2_separate_legs():
    content = (
        "= UEFA Champions League 2024/25\n"
        "▪ Playoffs, Matchday 1\n"
        "  Tue Feb 11\n"
        "    21:00  Manchester City FC (ENG)  v Real Madrid CF (ESP)  2-3 (1-0)\n"
        "▪ Playoffs, Matchday 2\n"
        "  Wed Feb 19\n"
        "    21:00  Real Madrid CF (ESP)  v Manchester City FC (ENG)  3-1 (2-0)\n"
    )
    parsed = parse(content)

    assert len(parsed["matches"]) == 2
    leg1, leg2 = parsed["matches"]
    assert leg1["stage"] == "KNOCKOUT_PLAYOFF"
    assert leg2["stage"] == "KNOCKOUT_PLAYOFF"
    assert leg1["matchday"] == 1
    assert leg2["matchday"] == 2
    assert leg1["date"] == "2025-02-11"
    assert leg2["date"] == "2025-02-19"
    # Two separate legs of the same tie, home/away swapped.
    assert (leg1["home_team"], leg1["away_team"]) == ("Manchester City FC", "Real Madrid CF")
    assert (leg2["home_team"], leg2["away_team"]) == ("Real Madrid CF", "Manchester City FC")


def test_knockout_round_headers_and_scores():
    content = (
        "= UEFA Champions League 2024/25\n"
        "▪ Finals, Round of 16\n"
        "  Tue Mar 4\n"
        "    21:00  Real Madrid CF (ESP)  v Club Atletico de Madrid (ESP)  2-1 (1-1)\n"
        "▪ Finals, Quarter-finals\n"
        "  Tue Apr 8\n"
        "    21:00  Arsenal FC (ENG)  v Real Madrid CF (ESP)  1-0 (0-0)\n"
        "▪ Finals, Semi-finals\n"
        "  Tue Apr 29\n"
        "    21:00  Arsenal FC (ENG)  v Paris Saint-Germain FC (FRA)  1-1 (0-1)\n"
        "▪ Finals, Final\n"
        "  Sat May 31\n"
        "    21:00  Paris Saint-Germain FC (FRA)  v FC Bayern Munchen (GER)  3-2 (2-1)\n"
    )
    parsed = parse(content)

    expected = [
        ("Round of 16", 2, 1, "Real Madrid CF", "Club Atletico de Madrid"),
        ("Quarter-finals", 1, 0, "Arsenal FC", "Real Madrid CF"),
        ("Semi-finals", 1, 1, "Arsenal FC", "Paris Saint-Germain FC"),
        ("Final", 3, 2, "Paris Saint-Germain FC", "FC Bayern Munchen"),
    ]
    assert [m["stage"] for m in parsed["matches"]] == ["KNOCKOUT"] * 4
    for match, (round_name, hs, as_, home, away) in zip(parsed["matches"], expected):
        assert match["round"] == round_name
        assert (match["home_score"], match["away_score"]) == (hs, as_)
        assert match["home_team"] == home
        assert match["away_team"] == away


def test_penalty_shootout_uses_underlying_match_score():
    content = (
        "= UEFA Champions League 2024/25\n"
        "▪ Finals, Round of 16\n"
        "  Tue Mar 11\n"
        "    21:00  Liverpool FC (ENG) v Paris Saint-Germain FC (FRA)  1-4 pen. 0-1 a.e.t. (0-1,0-1)\n"
    )
    parsed = parse(content)
    match = parsed["matches"][0]

    # The underlying 90+120 minute score is 0-1; the shootout 1-4 must NOT be
    # picked up as the match score.
    assert (match["home_score"], match["away_score"]) == (0, 1)
    assert match["home_team"] == "Liverpool FC"
    assert match["away_team"] == "Paris Saint-Germain FC"


# ---------------------------------------------------------------------------
# legacy group stage (kept green by repair)
# ---------------------------------------------------------------------------

def test_legacy_group_stage_header_supported():
    content = (
        "= UEFA Champions League 2022/23\n"
        "  Group A | Liverpool FC        Ajax        Napoli FC  Rangers FC\n"
        "  Wed Sep/7\n"
        "    21:00  Ajax (NED)  v Rangers (SCO)  4-0\n"
    )
    parsed = parse(content, season="2022-23")

    match = parsed["matches"][0]
    assert match["stage"] == "GROUP_STAGE"
    assert match["group_name"] == "Group A"
    assert match["date"] == "2022-09-07"
    assert (match["home_score"], match["away_score"]) == (4, 0)


# ---------------------------------------------------------------------------
# TeamMatcher: kickoff stripped before lookup/store
# ---------------------------------------------------------------------------

def test_time_prefixed_variants_resolve_same_existing_club(db):
    add_team(db, 5, "Liverpool FC")
    db.commit()
    matcher = TeamMatcher(db)

    first = matcher.find_or_create_team_id("21:00 Liverpool FC")
    second = matcher.find_or_create_team_id("18:45 Liverpool FC")

    # Both kicked-off variants must hit the real club row, not new synthetics.
    assert first == 5
    assert second == 5

    rows = db.fetchall("SELECT name FROM teams WHERE name LIKE '%iverpool%'")
    assert [r[0] for r in rows] == ["Liverpool FC"]


def test_time_variant_of_new_team_not_stored_as_artifact(db):
    matcher = TeamMatcher(db)

    team_id = matcher.find_or_create_team_id("20.45 Paris Saint-Germain")

    row = db.fetchone("SELECT name FROM teams WHERE id = ?", (team_id,))
    assert row[0] == "Paris Saint-Germain"
    assert "20.45" not in row[0]


def test_normalize_strips_kickoff_variants(db):
    matcher = TeamMatcher(db)

    assert matcher._normalize_team_name("21:00 Liverpool FC") == "liverpool"
    assert matcher._normalize_team_name("20.45 Liverpool FC") == "liverpool"
    assert matcher._normalize_team_name("Liverpool FC") == "liverpool"