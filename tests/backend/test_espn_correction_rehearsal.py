"""Hermetic tests for the copy-only correction rehearsal profile.

Every test uses real temporary DuckDB files and synthetic pinned ESPN/text
bodies. Nothing here touches the network, production paths, secrets, the
scheduler or the real audit snapshot.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import duckdb
import pytest

from backend import espn_correction_rehearsal as cr
from backend import espn_reconcile as rec
from backend import solkoff_calculator
from backend.database import Database
from backend.espn_client import ESPNClient
from tests.backend import espn_payloads as P

TEAMS = [
    (100, "AS Roma"),
    (-116527, "21:00 Lazio Roma"),
    (-119959, "Lazio"),
    (-154774, "Feyenoord"),
    (-269689, "Real Betis"),
    (-512950, "Shamrock Rovers"),
    (-267152, "Molde FK"),
    (-403009, "ACF Fiorentina"),
    (-497077, "Polissya Zhytomyr"),
    (-111, "Hibernian FC"),
    (65, "Manchester City FC"),
    (66, "Manchester United FC"),
    (-49777, "21:00 Manchester City FC"),
    (-222, "Lazio Opponent"),
]

MATCHES = [
    (-999204727, -403009, -497077, 3, 2, None, "2025-10-15", "FINISHED", None, None, None, "UCL"),
    (-805291009, -512950, -267152, 4, 5, None, "2025-03-13", "FINISHED", None, None, None, "UCL"),
    (-991989110, -116527, -269689, 1, 2, None, "2022-09-08", "FINISHED", None, None, None, "EL"),
    (-373301489, -116527, -154774, 4, 2, None, "2024-02-22", "FINISHED", None, None, None, "EL"),
    (-888800001, -116527, -222, 1, 1, None, "2023-09-20", "FINISHED", None, None, None, "EL"),
    (-888800002, -111, -497077, 0, 1, None, "2026-07-16", "FINISHED", None, None, None, "UCL"),
    (-888800003, -497077, -403009, 2, 4, None, "2026-07-16", "FINISHED", None, None, None, "UCL"),
    (-888800005, -116527, -111, 1, 0, None, "2022-05-05", "FINISHED", "KNOCKOUT", "Quarter-finals", None, "UCL"),
]

OLD_COMMIT = "a" * 40
GROUP_COMMIT = "c" * 40
ROOT_COMMIT = "b" * 40
OLD_URL = f"https://raw.githubusercontent.com/openfootball/champions-league/{OLD_COMMIT}/2023-24/el.txt"
GROUP_URL = f"https://raw.githubusercontent.com/openfootball/champions-league/{GROUP_COMMIT}/2022-23/el.txt"
ROOT_URL = f"https://raw.githubusercontent.com/openfootball/champions-league/{ROOT_COMMIT}/2025-26/conf.txt"
ELQ_URL = f"https://raw.githubusercontent.com/openfootball/champions-league/{ROOT_COMMIT}/2025-26/elq.txt"

OLD_LINES = [
    "▪ Playoffs",
    "  Thu Feb 22",
    "    21:00  AS Roma (ITA)           v Feyenoord (NED)          4-2 pen. 1-1 a.e.t. (1-1, 1-1)",
]
GROUP_LINES = [
    "▪ Group C",
    "  Thu Oct 6",
    "    21:00  AS Roma (ITA)           v Real Betis (ESP)         1-2 (1-1)",
]
ELQ_LINES = [
    "▪ 1. Round",
    "  Thu Jul 10",
    "    18:45  Hibernians FC (MLT)      v Polissya Zhytomyr (UKR)  1-0 (0-0)",
    "▪ 2. Round",
    "  Thu Jul 17",
    "    18:45  Hibernians FC (MLT)      v Polissya Zhytomyr (UKR)  2-1 (1-0)",
]
KO_LINES = [
    "▪ Semifinals",
    "  Thu May 5",
    "    21:00  AS Roma (ITA)           v Leicester City (ENG)     1-0 (1-0)",
]
ROOT_LINES = [
    "▪ Group A",
    "  Thu Oct 2",
    "    18:45  ACF Fiorentina (ITA)     v Polissya Zhytomyr (UKR)  3-2 (0-1)",
    "    18:45  Hibernians FC (MLT)      v Shamrock Rovers (IRL)    0-1 (0-0)",
    "  Thu Oct 9",
    "    18:45  Molde FK (NOR)           v AS Roma (ITA)            1-1 (0-0)",
    "    18:45  Real Betis (ESP)         v Lazio (ITA)              2-0 (1-0)",
]

QUAL_URL = "https://site.api.espn.com/apis/site/v2/sports/soccer/uefa.europa.conf_qual/scoreboard?dates=2025&limit=1000"
MAIN_UCL_URL = "https://site.api.espn.com/apis/site/v2/sports/soccer/uefa.europa.conf/scoreboard?dates=2025&limit=1000"
MAIN_EL_URL = "https://site.api.espn.com/apis/site/v2/sports/soccer/uefa.europa/scoreboard?dates=2022&limit=1000"

COUNTRY = {
    "109": "ITA", "22294": "UKR", "2499": "MLT", "112": "IRL",
    "11706": "NOR", "100": "ITA", "290": "ESP", "1120": "ITA",
}
NAMES = {
    "109": "ACF Fiorentina", "22294": "Polissya Zhytomyr",
    "2499": "Hibernians FC", "112": "Shamrock Rovers", "11706": "Molde FK",
    "100": "AS Roma", "290": "Real Betis", "1120": "Lazio",
}


def _with_country(event):
    for competition in event["competitions"]:
        for competitor in competition["competitors"]:
            team = competitor["team"]
            if team.get("id") in COUNTRY:
                team["country"] = COUNTRY[team["id"]]
                team["displayName"] = NAMES[team["id"]]
    return event


def _write_capture(directory: Path, rel: str, payload) -> tuple[str, str]:
    raw = json.dumps(payload).encode("utf-8")
    path = directory / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return rel, hashlib.sha256(raw).hexdigest()


def _build_captures(directory: Path) -> list[dict]:
    qual_events = [
        _with_country(P.event(9001, "2025-07-08T16:00Z", 2025, "first-round", 109, 22294, 3, 2, leg=1)),
        _with_country(P.event(9002, "2025-07-16T16:00Z", 2025, "first-round", 2499, 22294, 0, 1, leg=1)),
        _with_country(P.event(9003, "2025-07-16T18:00Z", 2025, "second-round", 22294, 109, 2, 2, leg=2)),
        _with_country(P.event(9004, "2025-07-08T18:00Z", 2025, "first-round", 1120, 22294, 0, 0, leg=1)),
        _with_country(P.event(9005, "2025-07-09T16:00Z", 2025, "first-round", 1120, 109, 3, 2, leg=1)),
    ]
    main_ucl = [
        _with_country(P.event(8001, "2025-03-13T20:00Z", 2024, "round-of-16", 112, 11706, 0, 1)),
    ]
    main_el = [
        _with_country(P.event(7001, "2022-10-06T19:00Z", 2022, "league-phase", 100, 290, 1, 2)),
    ]
    bodies = [
        ("qual-ucl.json", QUAL_URL, P.scoreboard_payload(qual_events, slug="uefa.europa.conf_qual"), "UCL", 2025),
        ("main-ucl.json", MAIN_UCL_URL, P.scoreboard_payload(main_ucl, slug="uefa.europa.conf"), "UCL", 2024),
        ("main-el.json", MAIN_EL_URL, P.scoreboard_payload(main_el, slug="uefa.europa"), "EL", 2022),
    ]
    entries = []
    for rel, url, payload, competition, season in bodies:
        rel, sha = _write_capture(directory, f"bodies/{rel}", payload)
        entries.append({
            "competition": competition, "season_year": season, "url": url,
            "sha256": sha, "path": rel, "collected_at": "2026-10-24T00:00:00Z",
        })
    (directory / "captures.json").write_text(
        json.dumps({"version": 1, "captures": entries}), encoding="utf-8"
    )
    return entries


def _make_source(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    db = Database(str(path))
    for team_id, name in TEAMS:
        db.execute("INSERT INTO teams (id, name, code, crest) VALUES (?, ?, NULL, NULL)",
                   (team_id, name))
    for row in MATCHES:
        db.execute(
            "INSERT INTO matches (id, home_team_id, away_team_id, home_score, "
            "away_score, matchday, date, status, stage, round, group_name, "
            "competition_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", row
        )
    db.execute(
        "INSERT INTO standings (competition_id, team_id, position, played, won, drawn, "
        "lost, goals_for, goals_against, goal_difference, points, last_updated) "
        "VALUES ('CL', 100, 1, 8, 6, 1, 1, 12, 5, 7, 19, '2026-10-15T00:00:00Z')"
    )
    db.execute(
        "INSERT INTO solkoff_coefficients (competition_id, team_id, solkoff_value, "
        "calculated_at) VALUES ('CL', 100, 9.9, '2026-10-15T00:00:00Z')"
    )
    db.execute(
        "INSERT INTO elo_ratings (team_id, rating, matches_played, updated_at) "
        "VALUES (100, 1111.0, 99, '2026-10-15T00:00:00Z')"
    )
    db.execute(
        "INSERT INTO provider_team_mappings (provider, external_id, team_id) "
        "VALUES ('espn', '116527', -116527)"
    )
    db.execute(
        "CREATE TABLE team_mappings (team_name TEXT PRIMARY KEY, team_id INTEGER, "
        "confidence REAL, source TEXT)"
    )
    db.execute(
        "INSERT INTO team_mappings (team_name, team_id, confidence, source) "
        "VALUES ('as roma', -116527, 1.0, 'matcher')"
    )
    db.commit()
    db.close()
    return path


def _row(path: Path, table: str, where: str, params: tuple) -> dict:
    conn = duckdb.connect(str(path), read_only=True)
    try:
        columns = [row[0] for row in conn.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = ? ORDER BY ordinal_position", (table,)
        ).fetchall()]
        row = conn.execute(f'SELECT * FROM "{table}" WHERE {where}', params).fetchone()
        return None if row is None else dict(zip(columns, row))
    finally:
        conn.close()


def _match(path: Path, match_id: int) -> dict:
    return _row(path, "matches", "id = ?", (match_id,))


def _team(path: Path, team_id: int) -> dict:
    return _row(path, "teams", "id = ?", (team_id,))


def _after(before: dict, fields: dict) -> dict:
    result = dict(before)
    result.update(fields)
    return result


def _raw_proof(lines, line_no, source_url, path_name, data) -> dict:
    return {
        "kind": "raw-text", "source_url": source_url, "path": path_name,
        "sha256": data.get("sha256", ""), "line": line_no,
        "line_text": lines[line_no - 1], "section": data["section"],
        "date": data["date"], "home_name": data["home_name"],
        "home_country": data["home_country"], "away_name": data["away_name"],
        "away_country": data["away_country"],
        "home_score": data["home_score"], "away_score": data["away_score"],
        "shootout_home": data.get("shootout_home"),
        "shootout_away": data.get("shootout_away"),
    }


def _main_proof(url: str, sha: str, event_id: str, season: int,
                competition: str, leg=None) -> dict:
    return {"kind": "espn-main", "url": url, "sha256": sha, "event_id": event_id,
            "season_year": season, "competition_id": competition, "leg": leg}


def _qual_proof(sha: str, event_id: str, season: int, competition: str,
                round_name: str, leg: int) -> dict:
    return {"kind": "espn-qualifier", "url": QUAL_URL, "sha256": sha,
            "event_id": event_id, "season_year": season,
            "competition_id": competition, "round": round_name, "leg": leg}


def _espn_team_proof(sha: str, event_id: str, season: int, competition: str,
                     external_id: str, identity_name: str, country: str,
                     url: str = QUAL_URL) -> dict:
    return {"kind": "espn-team", "url": url, "sha256": sha,
            "event_id": event_id, "season_year": season,
            "competition_id": competition, "external_id": external_id,
            "identity_name": identity_name, "country": country}


def _manifest(source: Path, captures: list[dict], raw_paths: dict) -> dict:
    inventory = cr._inspect_source(source)
    by_url = {entry["url"]: entry for entry in captures}
    qual_sha = by_url[QUAL_URL]["sha256"]
    main_sha = by_url[MAIN_UCL_URL]["sha256"]
    main_el_sha = by_url[MAIN_EL_URL]["sha256"]
    root_sha = hashlib.sha256(raw_paths["root"].read_bytes()).hexdigest()
    old_sha = hashlib.sha256(raw_paths["old"].read_bytes()).hexdigest()
    group_sha = hashlib.sha256(raw_paths["group"].read_bytes()).hexdigest()
    elq_sha = hashlib.sha256(raw_paths["elq"].read_bytes()).hexdigest()
    ko_sha = hashlib.sha256(raw_paths["ko"].read_bytes()).hexdigest()

    root_data = {
        3: {"section": "Group A", "date": "2025-10-02", "home_name": "ACF Fiorentina",
            "home_country": "ITA", "away_name": "Polissya Zhytomyr",
            "away_country": "UKR", "home_score": 3, "away_score": 2},
        4: {"section": "Group A", "date": "2025-10-02", "home_name": "Hibernians FC",
            "home_country": "MLT", "away_name": "Shamrock Rovers",
            "away_country": "IRL", "home_score": 0, "away_score": 1},
        6: {"section": "Group A", "date": "2025-10-09", "home_name": "Molde FK",
            "home_country": "NOR", "away_name": "AS Roma", "away_country": "ITA",
            "home_score": 1, "away_score": 1},
        7: {"section": "Group A", "date": "2025-10-09", "home_name": "Real Betis",
            "home_country": "ESP", "away_name": "Lazio", "away_country": "ITA",
            "home_score": 2, "away_score": 0},
    }
    for data in root_data.values():
        data["sha256"] = root_sha

    def root_proof(line: int, event_id: str, external: str, name: str,
                   country: str, event_season: int = 2025,
                   url: str = QUAL_URL, sha: str = qual_sha,
                   competition: str = "UCL", lines=ROOT_LINES,
                   source_url: str = ROOT_URL, path_name: str = "2025-26__conf.txt",
                   data: dict | None = None) -> list[dict]:
        data = dict(data or root_data[line])
        raw_identity = _raw_proof(lines, line, source_url, path_name, data)
        raw_identity["kind"] = "raw-identity"
        return [
            _espn_team_proof(sha, event_id, event_season, competition, external,
                             name, country, url=url),
            raw_identity,
        ]

    roots = [
        {"entry_id": "root-fiorentina", "external_id": "109",
         "canonical_team_id": -403009, "member_team_ids": [-403009],
         "identity_name": "ACF Fiorentina", "country": "ITA",
         "name_variants": ["ACF Fiorentina", "Fiorentina"],
         "proof": root_proof(3, "9001", "109", "ACF Fiorentina", "ITA"),
         "member_proofs": [], "expected_beforeimage": None},
        {"entry_id": "root-hibernians", "external_id": "2499",
         "canonical_team_id": 700000, "member_team_ids": [700000],
         "identity_name": "Hibernians FC", "country": "MLT",
         "name_variants": ["Hibernians FC", "Hibernians", "Hibernians FC (Malta)"],
         "proof": root_proof(3, "9002", "2499", "Hibernians FC", "MLT",
                             lines=ELQ_LINES, source_url=ELQ_URL,
                             path_name="2025-26__elq.txt",
                             data={"section": "1. Round", "date": "2025-07-10",
                                   "home_name": "Hibernians FC",
                                   "home_country": "MLT",
                                   "away_name": "Polissya Zhytomyr",
                                   "away_country": "UKR",
                                   "home_score": 1, "away_score": 0,
                                   "sha256": elq_sha}),
         "member_proofs": [], "expected_beforeimage": None},
        {"entry_id": "root-shamrock", "external_id": "112",
         "canonical_team_id": -512950, "member_team_ids": [-512950],
         "identity_name": "Shamrock Rovers", "country": "IRL",
         "name_variants": ["Shamrock Rovers"],
         "proof": root_proof(4, "8001", "112", "Shamrock Rovers", "IRL", 2024,
                             MAIN_UCL_URL, main_sha, "UCL"),
         "member_proofs": [], "expected_beforeimage": None},
        {"entry_id": "root-molde", "external_id": "11706",
         "canonical_team_id": -267152, "member_team_ids": [-267152],
         "identity_name": "Molde FK", "country": "NOR",
         "name_variants": ["Molde FK"],
         "proof": root_proof(6, "8001", "11706", "Molde FK", "NOR", 2024,
                             MAIN_UCL_URL, main_sha, "UCL"),
         "member_proofs": [], "expected_beforeimage": None},
        {"entry_id": "root-roma", "external_id": "100",
         "canonical_team_id": 100, "member_team_ids": [100],
         "identity_name": "AS Roma", "country": "ITA",
         "name_variants": ["AS Roma", "Roma"],
         "proof": root_proof(6, "7001", "100", "AS Roma", "ITA", 2022,
                             MAIN_EL_URL, main_el_sha, "EL"),
         "member_proofs": [], "expected_beforeimage": None},
        {"entry_id": "root-betis", "external_id": "290",
         "canonical_team_id": -269689, "member_team_ids": [-269689],
         "identity_name": "Real Betis", "country": "ESP",
         "name_variants": ["Real Betis", "Betis"],
         "proof": root_proof(7, "7001", "290", "Real Betis", "ESP", 2022,
                             MAIN_EL_URL, main_el_sha, "EL"),
         "member_proofs": [], "expected_beforeimage": None},
        {"entry_id": "root-lazio", "external_id": "1120",
         "canonical_team_id": -119959, "member_team_ids": [-119959, -116527],
         "identity_name": "Lazio Roma", "country": "ITA",
         "name_variants": ["Lazio Roma", "SS Lazio", "Lazio"],
         "proof": [
             _espn_team_proof(qual_sha, "9004", 2025, "UCL", "1120", "Lazio", "ITA"),
             dict(_raw_proof(ROOT_LINES, 7, ROOT_URL, "2025-26__conf.txt",
                             dict(root_data[7])), kind="raw-identity"),
         ],
         "member_proofs": [], "expected_beforeimage": None},
    ]

    second_round = _raw_proof(
        ELQ_LINES, 6, ELQ_URL, "2025-26__elq.txt",
        {"section": "2. Round", "date": "2025-07-17",
         "home_name": "Hibernians FC", "home_country": "MLT",
         "away_name": "Polissya Zhytomyr", "away_country": "UKR",
         "home_score": 2, "away_score": 1, "sha256": elq_sha},
    )
    second_round["kind"] = "raw-identity"
    roots[1]["proof"].append(second_round)

    old_data = {"section": "Playoffs", "date": "2024-02-22",
                "home_name": "AS Roma", "home_country": "ITA",
                "away_name": "Feyenoord", "away_country": "NED",
                "home_score": 1, "away_score": 1,
                "shootout_home": 4, "shootout_away": 2, "sha256": old_sha}
    group_data = {"section": "Group C", "date": "2022-10-06",
                  "home_name": "AS Roma", "home_country": "ITA",
                  "away_name": "Real Betis", "away_country": "ESP",
                  "home_score": 1, "away_score": 2, "sha256": group_sha}
    ko_data = {"section": "Semifinals", "date": "2022-05-05",
               "home_name": "AS Roma", "home_country": "ITA",
               "away_name": "Leicester City", "away_country": "ENG",
               "home_score": 1, "away_score": 0, "sha256": ko_sha}

    changes = {
        -999204727: ({"date": "2025-07-08", "stage": "QUALIFYING",
                      "round": "First Round"},
                     [_qual_proof(qual_sha, "9001", 2025, "UCL", "First Round", 1)]),
        -888800002: ({"date": "2025-07-16", "stage": "QUALIFYING",
                      "round": "First Round", "home_team_id": 700000},
                     [_qual_proof(qual_sha, "9002", 2025, "UCL", "First Round", 1)]),
        -888800003: ({"date": "2025-07-16", "stage": "QUALIFYING",
                      "round": "Second Round", "home_score": 2, "away_score": 2},
                     [_qual_proof(qual_sha, "9003", 2025, "UCL", "Second Round", 2)]),
        -805291009: ({"home_score": 0, "away_score": 1},
                     [_main_proof(MAIN_UCL_URL, main_sha, "8001", 2024, "UCL")]),
        -991989110: ({"home_team_id": 100},
                     [_raw_proof(GROUP_LINES, 3, GROUP_URL, "2022-23__el.txt",
                                 group_data)]),
        -373301489: ({"home_team_id": 100, "home_score": 1, "away_score": 1},
                     [_raw_proof(OLD_LINES, 3, OLD_URL, "2023-24__el.txt",
                                 old_data)]),
        -888800005: ({"home_team_id": 100},
                     [_raw_proof(KO_LINES, 3,
                                 "https://raw.githubusercontent.com/openfootball/champions-league/%s/2021-22/conf.txt" % ("d" * 40),
                                 "2021-22__conf.txt", ko_data)]),
    }
    bindings = {
        -999204727: [
            {"side": "home", "external_id": "109", "display_name": "ACF Fiorentina",
             "country": "ITA", "source_team_id": -403009, "team_id": -403009,
             "raw_name": "ACF Fiorentina", "raw_country": "ITA"},
            {"side": "away", "external_id": "22294", "display_name": "Polissya Zhytomyr",
             "country": "UKR", "source_team_id": -497077, "team_id": -497077,
             "raw_name": "Polissya Zhytomyr", "raw_country": "UKR"},
        ],
        -888800002: [
            {"side": "home", "external_id": "2499", "display_name": "Hibernians FC",
             "country": "MLT", "source_team_id": -111, "team_id": 700000,
             "raw_name": "Hibernians FC", "raw_country": "MLT"},
            {"side": "away", "external_id": "22294", "display_name": "Polissya Zhytomyr",
             "country": "UKR", "source_team_id": -497077, "team_id": -497077,
             "raw_name": "Polissya Zhytomyr", "raw_country": "UKR"},
        ],
        -888800003: [
            {"side": "home", "external_id": "22294", "display_name": "Polissya Zhytomyr",
             "country": "UKR", "source_team_id": -497077, "team_id": -497077,
             "raw_name": "Polissya Zhytomyr", "raw_country": "UKR"},
            {"side": "away", "external_id": "109", "display_name": "ACF Fiorentina",
             "country": "ITA", "source_team_id": -403009, "team_id": -403009,
             "raw_name": "ACF Fiorentina", "raw_country": "ITA"},
        ],
        -805291009: [
            {"side": "home", "external_id": "112", "display_name": "Shamrock Rovers",
             "country": "IRL", "source_team_id": -512950, "team_id": -512950,
             "raw_name": "Shamrock Rovers", "raw_country": "IRL"},
            {"side": "away", "external_id": "11706", "display_name": "Molde FK",
             "country": "NOR", "source_team_id": -267152, "team_id": -267152,
             "raw_name": "Molde FK", "raw_country": "NOR"},
        ],
    }
    match_ops = []
    for match_id, (change, evidence) in changes.items():
        before = _match(source, match_id)
        match_ops.append({
            "entry_id": f"match-{match_id}", "match_id": match_id,
            "expected_beforeimage": before, "expected_afterimage": _after(before, change),
            "fields": change, "evidence": evidence,
            "participant_bindings": bindings.get(match_id, []),
            "reason": "synthetic reviewed correction",
        })

    return {
        "version": cr.PROFILE_VERSION,
        "profile": cr.PROFILE,
        "source_sha256": inventory["source_sha256"],
        "source_path": str(source),
        "legacy_competition_default": inventory["legacy_competition_default"],
        "source_logical_hashes": inventory["source_logical_hashes"],
        "capture_index": "captures.json",
        "audit_season_year": 2026,
        "audit_season_bounds": {"start": "2026-08-01", "end": "2027-08-01"},
        "captures": [
            {"competition": entry["competition"], "season_year": entry["season_year"],
             "url": entry["url"], "sha256": entry["sha256"]}
            for entry in captures
        ],
        "expected": {
            "match_rows": len(MATCHES), "source_team_rows": len(TEAMS),
            "qualifying_corrections": 3, "wrong_club_slots": 1,
            "identity_corrections": 3, "legacy_nonidentity_exceptions": 1,
            "team_renames": 2, "created_team_rows": 1, "redirects": 1,
            "mixed_source_rows": 4, "mixed_split_rows": 3,
            "mixed_remaining_rows": 1,
        },
        "pending": {"main_stage_events": 342,
                    "full_takeover_clubs_unapproved": 151,
                    "note": "separate later gates; not part of this rehearsal"},
        "team_roots": roots,
        "team_creations": [{
            "entry_id": "create-maltese-hibernians", "team_id": 700000,
            "name": "Hibernians FC (Malta)", "identity_name": "Hibernians FC",
            "country": "MLT", "external_id": "2499",
            "reason": "ESPN 2499 is distinct from Scottish Hibernian 258",
            "proof": roots[1]["proof"],
        }],
        "team_renames": [
            {"entry_id": "rename-roma", "team_id": 100, "name": "AS Roma FC",
             "expected_beforeimage": _team(source, 100)},
            {"entry_id": "rename-lazio", "team_id": -119959, "name": "SS Lazio",
             "expected_beforeimage": _team(source, -119959)},
        ],
        "team_redirects": [
            {"entry_id": "redirect-mixed", "retired_team_id": -116527,
             "canonical_team_id": -119959,
             "reason": "mixed ID split first; only Lazio rows remain",
             "expected_beforeimage": None},
        ],
        "alias_corrections": [
            {"entry_id": "alias-as-roma", "team_name": "as roma", "team_id": 100,
             "reason": "cached wrong AS Roma target must not inherit Lazio",
             "expected_beforeimage": {
                 "team_name": "as roma", "team_id": -116527,
                 "confidence": 1.0, "source": "matcher",
             }},
        ],
        "reviewed_alias_bindings": [
            {"name": "AS Roma", "team_id": 100, "country": "ITA"},
            {"name": "Feyenoord", "team_id": -154774, "country": "NED"},
            {"name": "ACF Fiorentina", "team_id": -403009, "country": "ITA"},
            {"name": "Polissya Zhytomyr", "team_id": -497077, "country": "UKR"},
            {"name": "Hibernians FC", "team_id": 700000, "country": "MLT"},
            {"name": "Shamrock Rovers", "team_id": -512950, "country": "IRL"},
            {"name": "Molde FK", "team_id": -267152, "country": "NOR"},
            {"name": "Real Betis", "team_id": -269689, "country": "ESP"},
            {"name": "Lazio", "team_id": -119959, "country": "ITA"},
            {"name": "Leicester City", "team_id": -111, "country": "ENG"},
        ],
        "legacy_exception_allowlist": [
            {"match_id": -373301489,
             "allowed_fields": ["home_team_id", "home_score", "away_score"],
             "reason": "the one reviewed older Roma/Feyenoord exception"},
        ],
        "identity_exception_allowlist": [
            {"match_id": -991989110, "allowed_fields": ["home_team_id"],
             "reason": "mixed-id Roma split row"},
            {"match_id": -373301489, "allowed_fields": ["home_team_id"],
             "reason": "mixed-id Roma split row overlapping the older exception"},
            {"match_id": -888800005, "allowed_fields": ["home_team_id"],
             "reason": "mixed-id Roma split row with stale knockout round"},
        ],
        "metadata_discrepancies": [
            {"match_id": -991989110, "fields": ["date"],
             "reason": "stored collapsed date differs from pinned group fixture"},
            {"match_id": -888800005, "fields": ["round"],
             "reason": "stored knockout round differs from pinned Semifinals"},
        ],
        "mixed_identity_groups": [{
            "entry_id": "mixed-lazio-roma", "retired_team_id": -116527,
            "redirect_team_id": -119959, "expected_source_rows": 4,
            "destinations": [
                {"team_id": 100,
                 "match_ids": [-991989110, -373301489, -888800005],
                 "reason": "proved AS Roma rows"},
                {"team_id": -119959,
                 "match_ids": [-888800001],
                 "reason": "remaining proved Lazio rows"},
            ],
            "reason": "split every source incident before retiring the mixed id",
        }],
        "match_corrections": match_ops,
        "reference_rewiring": [
            {"entry_id": "rewire-matches", "table": "matches", "column": "home_team_id",
             "old_team_id": -116527, "new_team_id": -119959, "expected_count": 1,
             "reason": "remaining Lazio rows only"},
            {"entry_id": "rewire-provider", "table": "provider_team_mappings",
             "column": "team_id", "old_team_id": -116527, "new_team_id": -119959,
             "expected_count": 1, "reason": "provider mapping follows the root"},
        ],
    }


def _write_manifest(directory: Path, manifest: dict, name: str = "plan.json") -> Path:
    path = directory / name
    path.write_text(json.dumps(manifest, sort_keys=True), encoding="utf-8")
    review = {
        "version": cr.PROFILE_VERSION,
        "scope": cr.REVIEW_SCOPE,
        "profile": cr.PROFILE,
        "manifest_sha256": cr.manifest_sha256(manifest),
        "authorization": "user selected Prepare copy-only correction rehearsal",
        "technical_review": {
            "reviewer": "synthetic independent reviewer",
            "reviewed_at": "2026-10-24T00:00:00Z",
            "notes": "reviewed against pinned synthetic evidence",
        },
    }
    cr.review_receipt_path(path).write_text(json.dumps(review), encoding="utf-8")
    return path


@pytest.fixture()
def setup(tmp_path):
    directory = tmp_path / "private"
    directory.mkdir(mode=0o700)
    os.chmod(directory, 0o700)
    captures = _build_captures(directory)
    source = _make_source(directory / "source.db")
    raw = {}
    for key, name, lines in (
        ("old", "2023-24__el.txt", OLD_LINES),
        ("group", "2022-23__el.txt", GROUP_LINES),
        ("root", "2025-26__conf.txt", ROOT_LINES),
        ("elq", "2025-26__elq.txt", ELQ_LINES),
        ("ko", "2021-22__conf.txt", KO_LINES),
    ):
        path = directory / name
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        raw[key] = path
    return directory, source, captures, raw


def _build(setup, name: str = "plan.json", mutate=None):
    directory, source, captures, raw = setup
    manifest = _manifest(source, captures, raw)
    if mutate is not None:
        mutate(manifest)
    path = _write_manifest(directory, manifest, name)
    return manifest, path


def test_success_rehearses_and_preserves_source(setup):
    directory, source, _captures, _raw = setup
    source_sha = cr._file_sha256(source)
    manifest, manifest_path = _build(setup)
    output = directory / "candidate.db"
    receipt = cr.rehearse(str(source), str(manifest_path), str(output))

    assert receipt["state"] == "rehearsed", receipt
    assert receipt["rehearsal_only"] is True
    assert receipt["production_ready"] is False
    assert receipt["full_takeover"] is False
    assert receipt["audit_season_year"] == 2026
    assert receipt["audit_season_bounds"] == {"start": "2026-08-01", "end": "2027-08-01"}
    assert receipt["pending_gates"]["status"] == "declared_not_verified"
    assert "verified" not in receipt
    assert cr._file_sha256(source) == source_sha

    conn = duckdb.connect(str(output), read_only=True)
    try:
        assert conn.execute("SELECT COUNT(*) FROM matches").fetchone()[0] == len(MATCHES)
        assert conn.execute("SELECT COUNT(*) FROM teams").fetchone()[0] == len(TEAMS) + 1
        assert conn.execute(
            "SELECT stage, date, round FROM matches WHERE id = -999204727"
        ).fetchone() == ("QUALIFYING", "2025-07-08", "First Round")
        assert conn.execute(
            "SELECT home_team_id, home_score, away_score FROM matches WHERE id = -373301489"
        ).fetchone() == (100, 1, 1)
        assert conn.execute(
            "SELECT home_team_id FROM matches WHERE id = -991989110"
        ).fetchone()[0] == 100
        assert conn.execute(
            "SELECT home_team_id FROM matches WHERE id = -888800001"
        ).fetchone()[0] == -119959
        assert conn.execute(
            "SELECT name FROM teams WHERE id = 700000"
        ).fetchone()[0] == "Hibernians FC (Malta)"
        assert conn.execute(
            "SELECT points, played FROM standings WHERE competition_id='CL' AND team_id=100"
        ).fetchone() == (19, 8)
        assert conn.execute(
            "SELECT matches_played FROM elo_ratings WHERE team_id = -403009"
        ).fetchone()[0] >= 1
        assert conn.execute(
            "SELECT COUNT(*) FROM solkoff_coefficients WHERE team_id = -403009"
        ).fetchone()[0] == 0
    finally:
        conn.close()
    verified = cr.verify_candidate(str(output), str(manifest_path))
    assert verified["state"] == "rehearsed", verified["failures"]
    assert verified["production_ready"] is False


def test_preview_writes_no_candidate(setup):
    directory, source, _captures, _raw = setup
    source_sha = cr._file_sha256(source)
    _manifest_data, manifest_path = _build(setup)
    report = cr.preview(str(source), str(manifest_path))
    assert report["state"] == "previewed"
    assert report["rehearsal_only"] is True and report["production_ready"] is False
    assert not any(path.name.startswith("candidate") for path in directory.iterdir())
    assert cr._file_sha256(source) == source_sha



def test_root_variants_allow_short_provider_name_without_global_alias(setup):
    manifest_data, path = _build(setup)
    lazio = next(root for root in manifest_data["team_roots"]
                 if root["external_id"] == "1120")
    assert lazio["identity_name"] == "Lazio Roma"
    assert lazio["proof"][0]["identity_name"] == "Lazio"
    assert "Lazio" in lazio["name_variants"]
    receipt = cr.rehearse(str(setup[1]), str(path), str(setup[0] / "candidate.db"))
    assert receipt["state"] == "rehearsed"


def test_root_provider_name_must_be_reviewed_variant(setup):
    def mutate(manifest):
        lazio = next(root for root in manifest["team_roots"]
                     if root["external_id"] == "1120")
        lazio["name_variants"] = ["Lazio Roma"]
    _manifest_data, path = _build(setup, mutate=mutate)
    with pytest.raises(cr.RefusedError):
        cr.rehearse(str(setup[1]), str(path), str(setup[0] / "candidate.db"))


def test_root_variant_overlap_same_country_refused(setup):
    def mutate(manifest):
        lazio = next(root for root in manifest["team_roots"]
                     if root["external_id"] == "1120")
        lazio["name_variants"].append("Roma")
    _manifest_data, path = _build(setup, mutate=mutate)
    with pytest.raises(cr.RefusedError):
        cr.rehearse(str(setup[1]), str(path), str(setup[0] / "candidate.db"))


@pytest.mark.parametrize("field,value", [
    ("team_id", -111),
    ("raw_country", "FRA"),
    ("display_name", "Wrong Club"),
    ("source_team_id", -999),
])
def test_forged_participant_binding_refused(setup, field, value):
    def mutate(manifest):
        op = next(op for op in manifest["match_corrections"]
                  if op["match_id"] == -999204727)
        op["participant_bindings"][0][field] = value
    _manifest_data, path = _build(setup, mutate=mutate)
    with pytest.raises(cr.RefusedError):
        cr.rehearse(str(setup[1]), str(path), str(setup[0] / "candidate.db"))


def test_changed_slot_must_target_proven_root(setup):
    def mutate(manifest):
        op = next(op for op in manifest["match_corrections"]
                  if op["match_id"] == -888800002)
        op["fields"]["home_team_id"] = -111
        op["expected_afterimage"] = _after(op["expected_beforeimage"], op["fields"])
        op["participant_bindings"][0]["team_id"] = -111
        for binding in manifest["reviewed_alias_bindings"]:
            if binding["name"] == "Hibernians FC":
                binding["team_id"] = -111
    _manifest_data, path = _build(setup, mutate=mutate)
    with pytest.raises(cr.RefusedError):
        cr.rehearse(str(setup[1]), str(path), str(setup[0] / "candidate.db"))


def test_identity_only_old_round_drift_requires_declaration(setup):
    def mutate(manifest):
        manifest["metadata_discrepancies"] = [
            item for item in manifest["metadata_discrepancies"]
            if item["match_id"] != -888800005
        ]
    _manifest_data, path = _build(setup, mutate=mutate)
    with pytest.raises(cr.RefusedError):
        cr.rehearse(str(setup[1]), str(path), str(setup[0] / "candidate.db"))


def test_identity_only_group_and_qualifier_country_stem_valid(setup):
    manifest_data, path = _build(setup)
    op = next(op for op in manifest_data["match_corrections"]
              if op["match_id"] == -991989110)
    assert op["evidence"][0]["section"] == "Group C"
    assert op["fields"] == {"home_team_id": 100}
    mlt_root = next(root for root in manifest_data["team_roots"]
                    if root["external_id"] == "2499")
    assert "/2025-26/elq.txt" in mlt_root["proof"][1]["source_url"]
    receipt = cr.rehearse(str(setup[1]), str(path), str(setup[0] / "candidate.db"))
    assert receipt["state"] == "rehearsed"


def test_unchanged_opponent_not_in_global_roots_is_bound_per_match(setup):
    manifest_data, path = _build(setup)
    assert not any(root["external_id"] == "22294"
                   for root in manifest_data["team_roots"])
    op = next(op for op in manifest_data["match_corrections"]
              if op["match_id"] == -999204727)
    away = next(b for b in op["participant_bindings"] if b["side"] == "away")
    assert away["external_id"] == "22294"
    assert away["source_team_id"] == away["team_id"] == -497077
    receipt = cr.rehearse(str(setup[1]), str(path), str(setup[0] / "candidate.db"))
    assert receipt["state"] == "rehearsed"


def test_participant_binding_target_root_identity_must_match(setup):
    def mutate(manifest):
        op = next(op for op in manifest["match_corrections"]
                  if op["match_id"] == -999204727)
        home = op["participant_bindings"][0]
        home.update({"external_id": "100", "display_name": "AS Roma",
                     "country": "ITA", "raw_name": "AS Roma",
                     "raw_country": "ITA"})
        for binding in manifest["reviewed_alias_bindings"]:
            if binding["name"] == "AS Roma":
                binding["team_id"] = -403009
    _manifest_data, path = _build(setup, mutate=mutate)
    with pytest.raises(cr.RefusedError):
        cr.rehearse(str(setup[1]), str(path), str(setup[0] / "candidate.db"))


def test_unchanged_binding_raw_name_must_match_source(setup):
    def mutate(manifest):
        op = next(op for op in manifest["match_corrections"]
                  if op["match_id"] == -999204727)
        away = next(b for b in op["participant_bindings"] if b["side"] == "away")
        away["raw_name"] = "Other Club"
        manifest["reviewed_alias_bindings"].append(
            {"name": "Other Club", "team_id": -497077, "country": "UKR"}
        )
    _manifest_data, path = _build(setup, mutate=mutate)
    with pytest.raises(cr.RefusedError):
        cr.rehearse(str(setup[1]), str(path), str(setup[0] / "candidate.db"))


def test_binding_country_must_match_target_root(setup):
    def mutate(manifest):
        op = next(op for op in manifest["match_corrections"]
                  if op["match_id"] == -999204727)
        op["participant_bindings"][0]["country"] = "FRA"
    _manifest_data, path = _build(setup, mutate=mutate)
    with pytest.raises(cr.RefusedError):
        cr.rehearse(str(setup[1]), str(path), str(setup[0] / "candidate.db"))


def test_binding_display_name_must_match_provider(setup):
    def mutate(manifest):
        op = next(op for op in manifest["match_corrections"]
                  if op["match_id"] == -999204727)
        op["participant_bindings"][1]["display_name"] = "Polissya Zhitomir"
    _manifest_data, path = _build(setup, mutate=mutate)
    with pytest.raises(cr.RefusedError):
        cr.rehearse(str(setup[1]), str(path), str(setup[0] / "candidate.db"))


def test_mixed_group_and_second_root_omitted_refused(setup):
    manifest_data, _path = _build(setup)
    manifest_data["team_roots"] = [
        root for root in manifest_data["team_roots"]
        if root["canonical_team_id"] != 100
    ]
    manifest_data["mixed_identity_groups"] = []
    identity_by_team = {}
    for root in manifest_data["team_roots"]:
        for team_id in [root["canonical_team_id"]] + root["member_team_ids"]:
            identity_by_team[int(team_id)] = (root["identity_name"], root["country"])
    with pytest.raises(cr.RefusedError):
        cr._require_mixed_group_for_cross_alias(
            manifest_data, setup[1], {-116527: -119959}, identity_by_team
        )


def test_malformed_evidence_shape_refused_without_keyerror(setup):
    def mutate(manifest):
        manifest["match_corrections"][0]["evidence"][0].pop("url")
    _manifest_data, path = _build(setup, mutate=mutate)
    with pytest.raises(cr.RefusedError):
        cr.rehearse(str(setup[1]), str(path), str(setup[0] / "candidate.db"))


@pytest.mark.parametrize("field", ["name", "identity_name"])
def test_creation_cannot_duplicate_existing_club_name(setup, field):
    def mutate(manifest):
        manifest["team_creations"][0][field] = "Hibernian FC"
    _manifest_data, path = _build(setup, mutate=mutate)
    with pytest.raises(cr.RefusedError):
        cr.rehearse(str(setup[1]), str(path), str(setup[0] / "candidate.db"))


def test_identity_raw_semantic_duplicate_refused(setup):
    directory, source, captures, raw = setup
    raw["group"].write_text(
        "\n".join(GROUP_LINES + [
            "▪ Group D",
            "  Thu Oct 13",
            "    21:00  AS Roma (ITA)           v Real Betis (ESP)         1-2 (0-0)",
        ]) + "\n",
        encoding="utf-8",
    )
    _manifest_data, path = _build(setup)
    with pytest.raises(cr.RefusedError):
        cr.rehearse(str(source), str(path), str(directory / "candidate.db"))


def test_changed_target_does_not_use_legacy_field_bypass(setup):
    def mutate(manifest):
        op = next(op for op in manifest["match_corrections"]
                  if op["match_id"] == -999204727)
        op["fields"]["home_team_id"] = 66
        op["expected_afterimage"] = _after(op["expected_beforeimage"], op["fields"])
    _manifest_data, path = _build(setup, mutate=mutate)
    with pytest.raises(cr.RefusedError):
        cr.rehearse(str(setup[1]), str(path), str(setup[0] / "candidate.db"))


def test_root_canonical_country_cannot_be_proved_by_member(monkeypatch, setup):
    manifest_data, _path = _build(setup)
    root = next(root for root in manifest_data["team_roots"]
                if root["external_id"] == "1120")
    monkeypatch.setattr(
        cr, "_parse_espn_team_proof",
        lambda *args, **kwargs: {"identity_name": "Lazio", "country": None,
                                 "event_id": "9004", "side": "home",
                                 "external_id": "1120"},
    )
    aliases = {(cr._normal_name("Lazio Roma"), "ITA"): -116527}
    entries = cr._resolve_entries(manifest_data, setup[0])
    with pytest.raises(cr.RefusedError):
        cr._validate_identity_proofs(
            root["proof"], names={cr._variant_key(v) for v in root["name_variants"]},
            countries={"ITA"}, team_ids={root["canonical_team_id"]},
            external_id="1120", aliases=aliases,
            entries_by_url={e["url"]: e for e in entries},
            captures_dir=setup[0], what="root 1120",
        )


def test_qualifier_evidence_declared_round_must_match_event(setup):
    def mutate(manifest):
        op = next(op for op in manifest["match_corrections"]
                  if op["match_id"] == -999204727)
        op["evidence"][0]["round"] = "Second Round"
    _manifest_data, path = _build(setup, mutate=mutate)
    with pytest.raises(cr.RefusedError):
        cr.rehearse(str(setup[1]), str(path), str(setup[0] / "candidate.db"))


def test_stale_root_provider_beforeimage_refused_before_candidate(setup):
    directory, source, _captures, _raw = setup
    conn = duckdb.connect(str(source))
    try:
        conn.execute(
            "INSERT INTO provider_team_mappings (provider, external_id, team_id) "
            "VALUES ('espn', '109', 65)"
        )
        conn.commit()
    finally:
        conn.close()
    _manifest_data, path = _build(setup)
    output = directory / "candidate.db"
    with pytest.raises(cr.RefusedError):
        cr.rehearse(str(source), str(path), str(output))
    assert not output.exists()

def test_qualifier_evidence_cannot_pretend_main(setup):
    def mutate(manifest):
        manifest["match_corrections"][0]["evidence"][0]["kind"] = "espn-main"
        manifest["match_corrections"][0]["evidence"][0].pop("round")
    _manifest_data, path = _build(setup, mutate=mutate)
    with pytest.raises(cr.RefusedError):
        cr.rehearse(str(setup[1]), str(path), str(setup[0] / "candidate.db"))


def test_main_evidence_cannot_prove_qualifying_stage(setup):
    def mutate(manifest):
        op = next(op for op in manifest["match_corrections"] if op["match_id"] == -805291009)
        op["fields"]["stage"] = "QUALIFYING"
        op["expected_afterimage"] = _after(op["expected_beforeimage"], op["fields"])
    _manifest_data, path = _build(setup, mutate=mutate)
    with pytest.raises(cr.RefusedError):
        cr.rehearse(str(setup[1]), str(path), str(setup[0] / "candidate.db"))


@pytest.mark.parametrize("field,value", [
    ("date", "2025-07-09"),
    ("home_score", 9),
    ("away_team_id", 100),
])
def test_forged_qualifier_fields_refused(setup, field, value):
    def mutate(manifest):
        op = next(op for op in manifest["match_corrections"] if op["match_id"] == -999204727)
        op["fields"][field] = value
        op["expected_afterimage"] = _after(op["expected_beforeimage"], op["fields"])
    _manifest_data, path = _build(setup, mutate=mutate)
    with pytest.raises(cr.RefusedError):
        cr.rehearse(str(setup[1]), str(path), str(setup[0] / "candidate.db"))


def test_forged_right_score_wrong_fixture_refused(setup):
    def mutate(manifest):
        op = next(op for op in manifest["match_corrections"] if op["match_id"] == -999204727)
        op["evidence"][0]["event_id"] = "9005"
    _manifest_data, path = _build(setup, mutate=mutate)
    with pytest.raises(cr.RefusedError):
        cr.rehearse(str(setup[1]), str(path), str(setup[0] / "candidate.db"))


@pytest.mark.parametrize("field,value", [
    ("competition_id", "EL"),
    ("season_year", 2024),
    ("leg", 2),
])
def test_forged_event_identity_refused(setup, field, value):
    def mutate(manifest):
        op = next(op for op in manifest["match_corrections"] if op["match_id"] == -999204727)
        op["evidence"][0][field] = value
    _manifest_data, path = _build(setup, mutate=mutate)
    with pytest.raises(cr.RefusedError):
        cr.rehearse(str(setup[1]), str(path), str(setup[0] / "candidate.db"))


def test_raw_text_requires_explicit_allowlist(setup):
    def mutate(manifest):
        manifest["legacy_exception_allowlist"] = []
        manifest["identity_exception_allowlist"] = []
    _manifest_data, path = _build(setup, mutate=mutate)
    with pytest.raises(cr.RefusedError):
        cr.rehearse(str(setup[1]), str(path), str(setup[0] / "candidate.db"))


def test_raw_text_forged_date_refused(setup):
    def mutate(manifest):
        op = next(op for op in manifest["match_corrections"] if op["match_id"] == -373301489)
        op["evidence"][0]["date"] = "2024-02-23"
    _manifest_data, path = _build(setup, mutate=mutate)
    with pytest.raises(cr.RefusedError):
        cr.rehearse(str(setup[1]), str(path), str(setup[0] / "candidate.db"))


def test_raw_text_wrong_file_identity_refused(setup):
    def mutate(manifest):
        op = next(op for op in manifest["match_corrections"] if op["match_id"] == -373301489)
        op["evidence"][0]["path"] = "2022-23__el.txt"
    _manifest_data, path = _build(setup, mutate=mutate)
    with pytest.raises(cr.RefusedError):
        cr.rehearse(str(setup[1]), str(path), str(setup[0] / "candidate.db"))


def test_raw_text_forged_year_refused(setup):
    def mutate(manifest):
        op = next(op for op in manifest["match_corrections"] if op["match_id"] == -373301489)
        op["evidence"][0]["date"] = "2023-02-22"
    _manifest_data, path = _build(setup, mutate=mutate)
    with pytest.raises(cr.RefusedError):
        cr.rehearse(str(setup[1]), str(path), str(setup[0] / "candidate.db"))


def test_raw_text_group_identity_valid(setup):
    manifest_data, path = _build(setup)
    op = next(op for op in manifest_data["match_corrections"]
              if op["match_id"] == -991989110)
    assert op["evidence"][0]["section"] == "Group C"
    receipt = cr.rehearse(str(setup[1]), str(path), str(setup[0] / "candidate.db"))
    assert receipt["state"] == "rehearsed"


def test_raw_text_forged_country_refused(setup):
    def mutate(manifest):
        op = next(op for op in manifest["match_corrections"] if op["match_id"] == -373301489)
        op["evidence"][0]["home_country"] = "FRA"
    _manifest_data, path = _build(setup, mutate=mutate)
    with pytest.raises(cr.RefusedError):
        cr.rehearse(str(setup[1]), str(path), str(setup[0] / "candidate.db"))


def test_conflicting_overlapping_fields_refused(setup):
    def mutate(manifest):
        original = next(op for op in manifest["match_corrections"]
                        if op["match_id"] == -805291009)
        conflicting = json.loads(json.dumps(original))
        conflicting["entry_id"] = "conflicting"
        conflicting["fields"] = {"home_score": 3}
        conflicting["expected_afterimage"] = _after(
            conflicting["expected_beforeimage"], conflicting["fields"]
        )
        manifest["match_corrections"].append(conflicting)
    _manifest_data, path = _build(setup, mutate=mutate)
    with pytest.raises(cr.RefusedError):
        cr.rehearse(str(setup[1]), str(path), str(setup[0] / "candidate.db"))


def test_stale_beforeimage_refused(setup):
    def mutate(manifest):
        op = manifest["match_corrections"][0]
        op["expected_beforeimage"]["date"] = "1999-01-01"
    _manifest_data, path = _build(setup, mutate=mutate)
    with pytest.raises(cr.RefusedError):
        cr.rehearse(str(setup[1]), str(path), str(setup[0] / "candidate.db"))


def test_new_team_collision_refused(setup):
    def mutate(manifest):
        manifest["team_creations"][0]["team_id"] = 100
        for op in manifest["match_corrections"]:
            if op["fields"].get("home_team_id") == 700000:
                op["fields"]["home_team_id"] = 100
                op["expected_afterimage"] = _after(op["expected_beforeimage"], op["fields"])
    _manifest_data, path = _build(setup, mutate=mutate)
    with pytest.raises(cr.RefusedError):
        cr.rehearse(str(setup[1]), str(path), str(setup[0] / "candidate.db"))


def test_cross_club_root_redirect_refused(setup):
    def mutate(manifest):
        manifest["team_redirects"][0]["canonical_team_id"] = -512950
        manifest["mixed_identity_groups"][0]["redirect_team_id"] = -512950
        for op in manifest["reference_rewiring"]:
            op["new_team_id"] = -512950
    _manifest_data, path = _build(setup, mutate=mutate)
    with pytest.raises(cr.RefusedError):
        cr.rehearse(str(setup[1]), str(path), str(setup[0] / "candidate.db"))


def test_zero_split_whole_mixed_merge_refused(setup):
    def mutate(manifest):
        group = manifest["mixed_identity_groups"][0]
        group["destinations"] = [{
            "team_id": -119959,
            "match_ids": [-991989110, -373301489, -888800001],
            "reason": "illegal whole-id merge",
        }]
    _manifest_data, path = _build(setup, mutate=mutate)
    with pytest.raises(cr.RefusedError):
        cr.rehearse(str(setup[1]), str(path), str(setup[0] / "candidate.db"))


def test_omitted_mixed_group_refused_by_cross_club_alias(setup):
    def mutate(manifest):
        manifest["mixed_identity_groups"] = []
        manifest["expected"]["mixed_source_rows"] = 0
        manifest["expected"]["mixed_split_rows"] = 0
        manifest["expected"]["mixed_remaining_rows"] = 0
    _manifest_data, path = _build(setup, mutate=mutate)
    with pytest.raises(cr.RefusedError):
        cr.rehearse(str(setup[1]), str(path), str(setup[0] / "candidate.db"))


def test_mixed_group_omission_refused(setup):
    def mutate(manifest):
        manifest["mixed_identity_groups"][0]["destinations"][1]["match_ids"] = []
    _manifest_data, path = _build(setup, mutate=mutate)
    with pytest.raises(cr.RefusedError):
        cr.rehearse(str(setup[1]), str(path), str(setup[0] / "candidate.db"))


def test_mixed_group_cross_club_destination_refused(setup):
    def mutate(manifest):
        manifest["mixed_identity_groups"][0]["destinations"][0]["team_id"] = -111
    _manifest_data, path = _build(setup, mutate=mutate)
    with pytest.raises(cr.RefusedError):
        cr.rehearse(str(setup[1]), str(path), str(setup[0] / "candidate.db"))


def test_unrelated_candidate_modification_detected(setup):
    directory, source, _captures, _raw = setup
    _manifest_data, path = _build(setup)
    output = directory / "candidate.db"
    assert cr.rehearse(str(source), str(path), str(output))["state"] == "rehearsed"
    conn = duckdb.connect(str(output))
    try:
        conn.execute("UPDATE matches SET away_score = 9 WHERE id = -805291009")
        conn.commit()
    finally:
        conn.close()
    verified = cr.verify_candidate(str(output), str(path))
    assert verified["state"] == "rejected"
    assert "expected_state_matches" in verified["failures"]


def test_unrelated_standings_modification_detected(setup):
    directory, source, _captures, _raw = setup
    _manifest_data, path = _build(setup)
    output = directory / "candidate.db"
    assert cr.rehearse(str(source), str(path), str(output))["state"] == "rehearsed"
    conn = duckdb.connect(str(output))
    try:
        conn.execute(
            "UPDATE standings SET points = 20 WHERE competition_id = 'CL' AND team_id = 100"
        )
        conn.commit()
    finally:
        conn.close()
    verified = cr.verify_candidate(str(output), str(path))
    assert verified["state"] == "rejected"
    assert "expected_state_standings" in verified["failures"]


def test_dangling_standings_reference_detected(setup):
    directory, source, _captures, _raw = setup
    _manifest_data, path = _build(setup)
    output = directory / "candidate.db"
    assert cr.rehearse(str(source), str(path), str(output))["state"] == "rehearsed"
    conn = duckdb.connect(str(output))
    try:
        conn.execute("CREATE TABLE standings_loose AS SELECT * FROM standings")
        conn.execute("DROP TABLE standings")
        conn.execute("ALTER TABLE standings_loose RENAME TO standings")
        conn.execute(
            "INSERT INTO standings (competition_id, team_id, points) VALUES ('CL', 999999, 1)"
        )
        conn.commit()
    finally:
        conn.close()
    verified = cr.verify_candidate(str(output), str(path))
    assert verified["state"] == "rejected"
    assert "dangling_reference_standings_team_id" in verified["failures"]


def test_deleted_match_detected(setup):
    directory, source, _captures, _raw = setup
    _manifest_data, path = _build(setup)
    output = directory / "candidate.db"
    assert cr.rehearse(str(source), str(path), str(output))["state"] == "rehearsed"
    conn = duckdb.connect(str(output))
    try:
        conn.execute("DELETE FROM matches WHERE id = -888800001")
        conn.commit()
    finally:
        conn.close()
    verified = cr.verify_candidate(str(output), str(path))
    assert verified["state"] == "rejected"
    assert "match_id_set_unchanged" in verified["failures"]


def test_existing_output_and_sidecars_refused(setup):
    directory, source, _captures, _raw = setup
    _manifest_data, path = _build(setup)
    output = directory / "candidate.db"
    output.write_text("existing", encoding="utf-8")
    with pytest.raises(cr.RefusedError):
        cr.rehearse(str(source), str(path), str(output))
    output.unlink()
    wal = directory / "candidate.db.wal"
    wal.write_text("wal", encoding="utf-8")
    with pytest.raises(cr.RefusedError):
        cr.rehearse(str(source), str(path), str(output))


def test_public_output_parent_refused(setup):
    directory, source, _captures, _raw = setup
    _manifest_data, path = _build(setup)
    public = directory / "public"
    public.mkdir()
    os.chmod(public, 0o755)
    with pytest.raises(cr.RefusedError):
        cr.rehearse(str(source), str(path), str(public / "candidate.db"))


def test_failed_phase_leaves_rejected_receipt(setup):
    def mutate(manifest):
        manifest["reference_rewiring"][0]["expected_count"] = 99
    directory, source, _captures, _raw = setup
    _manifest_data, path = _build(setup, mutate=mutate)
    output = directory / "candidate.db"
    receipt = cr.rehearse(str(source), str(path), str(output))
    assert receipt["state"] == "rejected"
    assert (directory / "candidate.db.rehearsal-receipt.json").is_file()


def test_capture_tamper_refused(setup):
    directory, source, _captures, _raw = setup
    _manifest_data, path = _build(setup)
    body = directory / "bodies" / "qual-ucl.json"
    body.write_bytes(body.read_bytes() + b" ")
    with pytest.raises(cr.RefusedError):
        cr.rehearse(str(source), str(path), str(directory / "candidate.db"))


def test_stale_source_hash_refused(setup):
    directory, source, _captures, _raw = setup
    _manifest_data, path = _build(setup)
    conn = duckdb.connect(str(source))
    try:
        conn.execute("UPDATE teams SET name = 'Changed' WHERE id = 65")
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(cr.RefusedError):
        cr.rehearse(str(source), str(path), str(directory / "candidate.db"))


def test_expected_counts_must_match_proofs(setup):
    def mutate(manifest):
        manifest["expected"]["qualifying_corrections"] = 2
    _manifest_data, path = _build(setup, mutate=mutate)
    with pytest.raises(cr.RefusedError):
        cr.rehearse(str(setup[1]), str(path), str(setup[0] / "candidate.db"))


def test_pinned_audit_season_required(setup):
    def mutate(manifest):
        manifest["audit_season_bounds"]["start"] = "2026-07-01"
    _manifest_data, path = _build(setup, mutate=mutate)
    with pytest.raises(cr.RefusedError):
        cr.rehearse(str(setup[1]), str(path), str(setup[0] / "candidate.db"))


def test_audit_season_insulates_fingerprint_from_wall_clock(setup, monkeypatch):
    directory, source, _captures, _raw = setup
    _manifest_data, path = _build(setup)
    output = directory / "candidate.db"
    assert cr.rehearse(str(source), str(path), str(output))["state"] == "rehearsed"
    before = cr._candidate_fingerprint(str(output))

    class FakeDateTime:
        @classmethod
        def utcnow(cls):
            return __import__("datetime").datetime(2032, 1, 1)

    monkeypatch.setattr(solkoff_calculator, "datetime", FakeDateTime)
    verified = cr.verify_candidate(str(output), str(path))
    assert verified["state"] == "rehearsed", verified["failures"]
    assert verified["candidate_logical_fingerprint"] == before


def test_generic_review_receipt_is_not_accepted(setup):
    directory, source, captures, raw = setup
    manifest = _manifest(source, captures, raw)
    path = directory / "plan.json"
    path.write_text(json.dumps(manifest, sort_keys=True), encoding="utf-8")
    generic = directory / "plan.review.json"
    generic.write_text(json.dumps({"manifest_sha256": cr.manifest_sha256(manifest)}),
                       encoding="utf-8")
    with pytest.raises(cr.RefusedError):
        cr.rehearse(str(source), str(path), str(directory / "candidate.db"))


def test_review_scope_mismatch_refused(setup):
    directory, source, captures, raw = setup
    manifest = _manifest(source, captures, raw)
    path = _write_manifest(directory, manifest)
    review = json.loads(cr.review_receipt_path(path).read_text())
    review["scope"] = "full-takeover"
    cr.review_receipt_path(path).write_text(json.dumps(review), encoding="utf-8")
    with pytest.raises(cr.RefusedError):
        cr.preview(str(source), str(path))


def test_full_takeover_validator_rejects_profile(setup):
    source, captures, raw = setup[1], setup[2], setup[3]
    manifest = _manifest(source, captures, raw)
    with pytest.raises(rec.RefusedError):
        rec.validate_manifest(manifest, {})


def test_no_http_is_attempted(setup, monkeypatch):
    directory, source, _captures, _raw = setup
    _manifest_data, path = _build(setup)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("network access attempted")

    monkeypatch.setattr(ESPNClient, "_request", forbidden)
    receipt = cr.rehearse(str(source), str(path), str(directory / "candidate.db"))
    assert receipt["state"] == "rehearsed"


def test_cli_has_no_apply_command():
    parser = cr._build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["apply"])
