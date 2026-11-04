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
