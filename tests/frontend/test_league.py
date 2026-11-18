"""League view: search, sorting, columns, badges and preserved details."""
from __future__ import annotations

import re

import pytest
from playwright.sync_api import expect


def _team_rows(page):
    return page.locator("#standingsBody tr[data-team-id]")


def _visible_team_names(page):
    rows = page.locator("#standingsBody tr[data-team-id]:visible")
    names = []
    for i in range(rows.count()):
        names.append(rows.nth(i).inner_text())
    return names


def _first_team(page):
    return _team_rows(page).first.inner_text()


def _choose_sort(page, name):
    select = page.locator("#leagueSort")
    expect(select).to_be_visible()
    try:
        select.select_option(name.lower())
    except Exception:
        select.select_option(label=name)


def test_search_is_case_insensitive(open_app, page):
    open_app("league")
    expect(page.locator("#standingsTable")).to_be_visible()
    expect(_team_rows(page).first).to_be_visible()

    search = page.locator("#teamSearch")
    expect(search).to_be_visible()
    search.fill("alpha")
    visible = _visible_team_names(page)
    assert len(visible) == 1, visible
    assert "Alpha Strength FC" in visible[0]

    search.fill("ALPHA")
    assert "Alpha Strength FC" in _visible_team_names(page)[0]


def test_search_does_not_mutate_raw_data(open_app, page):
    open_app("league")
    search = page.locator("#teamSearch")
    expect(search).to_be_visible()
    search.fill("alpha")
    assert len(_visible_team_names(page)) == 1
    search.fill("")
    assert len(_visible_team_names(page)) == 26


def test_sort_selector_sets_metric_descending(open_app, page):
    open_app("league")
    expect(page.locator("#standingsTable")).to_be_visible()

    _choose_sort(page, "Solkoff")
    assert "Beta Schedule FC" in _first_team(page)

    _choose_sort(page, "Points")
    assert "Gamma Pinnacle" in _first_team(page)

    _choose_sort(page, "Strength")
    assert "Alpha Strength FC" in _first_team(page)


def test_header_click_cycles_desc_asc_removal(open_app, page):
    open_app("league")
    expect(page.locator("#league")).to_be_visible()
    strength_header = page.locator(
        "#standingsTable th", has_text=re.compile(r"^Strength$")
    ).first
    expect(strength_header).to_be_visible()

    # Default is strength descending -> Alpha first.
    assert "Alpha Strength FC" in _first_team(page)

    # First click toggles to ascending -> weakest team first.
    strength_header.click()
    assert "Filler 25" in _first_team(page)

    # Second click removes the sort -> falls back to default strength desc.
    strength_header.click()
    assert "Alpha Strength FC" in _first_team(page)


def test_header_customization_marks_selector_custom(open_app, page):
    open_app("league")
    header = page.locator(
        "#standingsTable th", has_text=re.compile(r"^Strength$")
    ).first
    expect(header).to_be_visible()
    header.click()
    select = page.locator("#leagueSort")
    value = (select.input_value() or "").lower()
    selected_text = select.evaluate(
        "el => el.options[el.selectedIndex] ? el.options[el.selectedIndex].text : ''"
    ).lower()
    assert "custom" in value or "custom" in selected_text


def test_advanced_toggle_controls_detail_columns(open_app, page):
    open_app("league")
    toggle = page.locator("#advancedToggle")
    expect(toggle).to_be_visible()
    toggle.uncheck()
    for label in ("W", "D", "L", "GF", "GA", "GD"):
        header = page.locator(
            "#standingsTable th", has_text=re.compile(rf"^{label}$")
        ).first
        expect(header).to_be_hidden()
    toggle.check()
    for label in ("W", "GF", "GD"):
        header = page.locator(
            "#standingsTable th", has_text=re.compile(rf"^{label}$")
        ).first
        expect(header).to_be_visible()


def _row_badge(page, team_name):
    """Qualification badge inside the team's standings row, never the legend."""
    row = page.locator(
        "#standingsBody tr[data-team-id]", has_text=team_name).first
    return row.locator(".zone-badge").first


def _assert_zone(page, team_name, cls, label_regex):
    row = page.locator(
        "#standingsBody tr[data-team-id]", has_text=team_name).first
    expect(row.locator(f".zone-badge.{cls}")).to_have_count(1)
    expect(row.locator(".zone-badge")).to_have_text(
        re.compile(label_regex, re.I))


def test_qualification_zones_follow_official_position(open_app, page):
    open_app("league")
    league = page.locator("#league")
    expect(league).to_contain_text(re.compile(r"direct", re.I))
    expect(league).to_contain_text(re.compile(r"play-?off", re.I))
    expect(league).to_contain_text(re.compile(r"eliminat", re.I))

    # Zones come from the OFFICIAL position badge, never the current sort
    # order. Under the default Strength ranking these positions are heavily
    # shuffled (Omega is #2 by strength yet official position 20), so
    # row-order checks are meaningless: assert each team's own badge instead.
    _assert_zone(page, "Beta Schedule FC", "zone-direct",
                 r"direct")           # official position 8
    _assert_zone(page, "Filler 09", "zone-playoff",
                 r"play-?off")        # official position 9
    _assert_zone(page, "Omega Playoff", "zone-playoff",
                 r"play-?off")        # strength #2, official position 20
    _assert_zone(page, "Filler 25", "zone-out",
                 r"eliminated")       # above the play-off cut-off


def test_default_strength_order_alpha_before_omega_and_no_divider_rows(open_app, page):
    """Default sort stays Strength descending; badge zones replace dividers.

    The strength order must survive (Alpha 99.9 first, Omega 90.0 second)
    instead of falling back to official position order, and the tbody holds
    exactly one row per team: any divider row would break the counts below.
    """
    open_app("league")
    rows = page.locator("#standingsBody tr[data-team-id]")
    expect(rows).to_have_count(26)  # one row per fixture team, no dividers
    expect(page.locator("#standingsBody tr[data-team-id] .zone-badge"))\
        .to_have_count(26)
    # Belt-and-braces: no zone-divider / qualification-divider classes exist
    # either (the legend's badges live outside #standingsBody).
    expect(page.locator(
        "#standingsBody tr.zone-divider, "
        "#standingsBody tr.qualification-round-of-16, "
        "#standingsBody tr.qualification-knockout")).to_have_count(0)

    first = rows.nth(0).inner_text()
    second = rows.nth(1).inner_text()
    assert "Alpha Strength FC" in first, first
    assert "Omega Playoff" in second, second


def test_sorting_and_search_do_not_reclassify_zones(open_app, page):
    open_app("league")

    # Solkoff sort tops the alternate ranking with Beta (1.90); its official
    # position 8 badge and every other zone must stay unchanged.
    _choose_sort(page, "Solkoff")
    expect(page.locator("#standingsBody tr[data-team-id]").first)\
        .to_contain_text("Beta Schedule FC")
    _assert_zone(page, "Beta Schedule FC", "zone-direct", r"direct")
    _assert_zone(page, "Omega Playoff", "zone-playoff", r"play-?off")
    _assert_zone(page, "Filler 25", "zone-out", r"eliminated")

    # Filtering down to one team must keep its badge honest too.
    page.locator("#teamSearch").fill("omega")
    expect(page.locator("#standingsBody tr[data-team-id]")).to_have_count(1)
    _assert_zone(page, "Omega Playoff", "zone-playoff", r"play-?off")


def test_expansion_survives_toolbar_rerender(open_app, page, api,
                                             build_solkoff_details):
    api.set_solkoff(5, build_solkoff_details(
        5, "Alpha Strength FC", "STR", "https://crest.example/str.png"))
    open_app("league")
    row = page.locator("#standingsBody tr", has_text="Alpha Strength FC").first
    expect(row).to_be_visible()
    row.click()
    expect(page.locator("#standingsBody")).to_contain_text("Opponent United")

    _choose_sort(page, "Points")
    expect(page.locator("#standingsBody")).to_contain_text("Opponent United")


def test_solkoff_details_endpoint_used_for_expansion(open_app, page, api,
                                                     build_solkoff_details):
    api.set_solkoff(1, build_solkoff_details(
        1, "Gamma Pinnacle", "PIN", None))
    open_app("league")
    row = page.locator("#standingsBody tr", has_text="Gamma Pinnacle").first
    expect(row).to_be_visible()
    row.click()
    expect(page.locator("#standingsBody")).to_contain_text("Opponent United")
    assert api.counts["GET /api/teams/1/solkoff-details"] >= 1


def test_skeleton_shown_while_standings_load(open_app, page, api):
    api.hold_path("/api/standings")
    open_app("league")
    expect(page.locator("#league")).to_be_visible()
    expect(page.locator(".skeleton-row").first).to_be_visible()
    api.release_held()
    expect(page.locator("#standingsBody tr[data-team-id]").first).to_be_visible()
    expect(page.locator(".skeleton-row")).to_have_count(0)


def test_skip_link_footer_and_shell_retained(open_app, page):
    open_app("league")
    expect(page.locator("#overview")).to_have_count(1)
    skip = page.locator(".skip-link").first
    expect(skip).to_have_attribute("href", "#main-content")
    expect(page.locator("footer")).to_contain_text("Linards Kalvans")


def test_intro_banner_dismissal_persisted(open_app, page, context):
    context.add_init_script("localStorage.setItem('solkoff-intro-seen', '1')")
    open_app("league")
    expect(page.locator("#overview")).to_have_count(1)
    expect(page.locator("#intro-banner")).to_be_hidden()


# ---------------------------------------------------------------------------
# Next confirmed league-phase opponent (task 2)
# ---------------------------------------------------------------------------

def _with_next_fixture(standings, team_id, **fixture):
    for team in standings:
        if team["teamId"] == team_id:
            team["nextFixture"] = fixture
            return standings
    raise AssertionError(f"fixture team {team_id} not found")


def _next_opponent(page, team_id):
    return page.locator(
        f"#standingsBody tr[data-team-id='{team_id}'] .next-opponent")


def test_next_opponent_shows_venue_ppg_and_sample(open_app, page, api,
                                                  standings):
    _with_next_fixture(
        standings, 5,
        opponentId=101, opponentName="Club X", venue="away",
        opponentPpg=2.0, opponentPlayed=5,
    )
    api.set_standings(standings)
    open_app("league")
    expect(page.locator("#standingsTable")).to_be_visible()

    opponent = _next_opponent(page, 5)
    expect(opponent).to_have_text("Next: Club X · Away · 2.00 PPG from 5 matches")
    # Supplemental text lives below the team name inside the existing cell,
    # never in a new column.
    row = page.locator("#standingsBody tr[data-team-id='5']")
    expect(row.locator(".team-cell .team-name + .next-opponent")).to_have_count(1)
    assert row.locator("td").count() == 12
    assert page.locator("#standingsTable thead th").count() == 12


def test_next_opponent_without_ppg_keeps_fixture(open_app, page, api,
                                                standings):
    _with_next_fixture(
        standings, 5,
        opponentId=102, opponentName="Club Y", venue="home",
        opponentPpg=None, opponentPlayed=None,
    )
    api.set_standings(standings)
    open_app("league")

    expect(_next_opponent(page, 5)).to_have_text("Next: Club Y · Home")


def test_next_opponent_uses_singular_match_sample(open_app, page, api,
                                                  standings):
    _with_next_fixture(
        standings, 5,
        opponentId=103, opponentName="Club X", venue="away",
        opponentPpg=2.0, opponentPlayed=1,
    )
    api.set_standings(standings)
    open_app("league")

    expect(_next_opponent(page, 5)).to_have_text(
        "Next: Club X · Away · 2.00 PPG from 1 match")


def test_next_opponent_zero_ppg_is_not_omitted(open_app, page, api, standings):
    _with_next_fixture(
        standings, 5,
        opponentId=104, opponentName="Club Z", venue="home",
        opponentPpg=0.0, opponentPlayed=4,
    )
    api.set_standings(standings)
    open_app("league")

    expect(_next_opponent(page, 5)).to_have_text(
        "Next: Club Z · Home · 0.00 PPG from 4 matches")


def test_next_opponent_absent_or_null_renders_nothing(open_app, page, api,
                                                     standings):
    # One explicit null, one field the server omitted entirely.
    for team in standings:
        if team["teamId"] == 5:
            team["nextFixture"] = None
    api.set_standings(standings)
    open_app("league")
    expect(page.locator("#standingsBody tr[data-team-id]").first).to_be_visible()

    expect(_next_opponent(page, 5)).to_have_count(0)
    expect(_next_opponent(page, 6)).to_have_count(0)
    expect(page.locator("#standingsBody .next-opponent")).to_have_count(0)


def test_next_opponent_name_is_escaped(open_app, page, api, standings):
    payload = '<img src=x onerror="window.__pwned=1">'
    _with_next_fixture(
        standings, 5,
        opponentId=105, opponentName=payload, venue="away",
        opponentPpg=None, opponentPlayed=None,
    )
    api.set_standings(standings)
    open_app("league")

    opponent = _next_opponent(page, 5)
    expect(opponent).to_have_text(f"Next: {payload} · Away")
    expect(opponent.locator("img")).to_have_count(0)
    assert page.evaluate("() => window.__pwned") is None


def test_search_ignores_next_opponent_text(open_app, page, api, standings):
    _with_next_fixture(
        standings, 5,
        opponentId=106, opponentName="Searchonly FC", venue="home",
        opponentPpg=1.5, opponentPlayed=2,
    )
    api.set_standings(standings)
    open_app("league")
    expect(_next_opponent(page, 5)).to_be_visible()

    search = page.locator("#teamSearch")
    search.fill("searchonly")
    expect(page.locator("#standingsBody tr[data-team-id]:visible")).to_have_count(0)

    # Filtering still matches the club's own name.
    search.fill("alpha")
    visible = page.locator("#standingsBody tr[data-team-id]:visible")
    expect(visible).to_have_count(1)
    expect(visible.first).to_contain_text("Alpha Strength FC")

