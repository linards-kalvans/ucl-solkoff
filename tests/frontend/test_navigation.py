"""Navigation, routing and stage-tab behaviour (UCL Strength Lab)."""
from __future__ import annotations

import re

import pytest
from playwright.sync_api import expect

STAGE_HASHES = [
    ("knockout-playoff", r"knockout play-?off"),
    ("round-of-16", r"round of 16"),
    ("quarter-final", r"quarter-?final"),
    ("semi-final", r"semi-?final"),
    ("final", r"final"),
]

# DOM (not CSS ``order``) sequence the knockout stage tabs must render in.
CHRONOLOGICAL_TAB_LABELS = [
    "play-off",
    "round of 16",
    "quarter-final",
    "semi-final",
    "final",
]


def _normalize_tab_label(text: str) -> str:
    label = re.sub(r"\s+", " ", (text or "").strip().lower())
    return re.sub(r"^knockout\s+", "", label)


def _selected(locator) -> bool:
    aria = locator.get_attribute("aria-selected")
    if aria is not None:
        return aria == "true"
    current = locator.get_attribute("aria-current")
    if current is not None:
        return current in ("page", "true")
    return "active" in (locator.get_attribute("class") or "")


def _assert_view(page, view_id):
    expect(page.locator(f"#{view_id}")).to_be_visible()
    for other in ("overview", "league", "knockout"):
        if other != view_id:
            expect(page.locator(f"#{other}")).to_be_hidden()


def test_overview_is_default_view_without_hash(open_app, page):
    open_app("")
    _assert_view(page, "overview")


def test_explicit_overview_hash_opens_overview(open_app, page):
    open_app("overview")
    _assert_view(page, "overview")


def test_league_hash_opens_league(open_app, page):
    open_app("league")
    _assert_view(page, "league")


def test_knockout_hash_opens_knockout(open_app, page):
    open_app("knockout")
    _assert_view(page, "knockout")


@pytest.mark.parametrize("hash_fragment,label", STAGE_HASHES)
def test_legacy_stage_hash_opens_knockout_with_stage(open_app, stage_tab, page,
                                                    hash_fragment, label):
    open_app(hash_fragment)
    _assert_view(page, "knockout")
    tab = stage_tab(page, label)
    expect(tab).to_be_visible()
    assert _selected(tab), f"{hash_fragment} should select the {label} stage tab"


def test_stage_api_does_not_override_overview_default(open_app, page, api):
    api.set_stage("FINAL")
    open_app("")
    _assert_view(page, "overview")
    expect(page.locator("#knockout")).to_be_hidden()


def test_stage_api_does_not_override_explicit_primary_hash(open_app, page, api):
    api.set_stage("FINAL")
    open_app("league")
    _assert_view(page, "league")


def test_stage_api_sets_initial_knockout_stage(open_app, stage_tab, page, api):
    api.set_stage("QUARTER_FINAL")
    open_app("knockout")
    expect(stage_tab(page, r"quarter-?final")).to_be_visible()
    assert _selected(stage_tab(page, r"quarter-?final"))


def test_late_current_stage_response_cannot_override_user_choice(open_app, stage_tab,
                                                                 page, api,
                                                                 wait_until):
    api.set_stage("FINAL")
    api.hold_path("/api/tournament/current-stage")
    open_app("knockout")
    _assert_view(page, "knockout")
    # User explicitly chooses Quarter-final before the stage API resolves.
    page.locator("#knockout [role='tab']",
                 has_text=re.compile(r"quarter-?final", re.I)).first.click()
    wait_until(lambda: api.held_count >= 1)
    api.release_held()
    quarter = stage_tab(page, r"quarter-?final")
    expect(quarter).to_be_visible()
    assert _selected(quarter)


def test_explicit_stage_hash_overrides_stage_api(open_app, stage_tab, page, api):
    api.set_stage("FINAL")
    open_app("round-of-16")
    _assert_view(page, "knockout")
    tab = stage_tab(page, r"round of 16")
    expect(tab).to_be_visible()
    assert _selected(tab)


def test_league_stage_api_defaults_to_round_of_16(open_app, stage_tab, page, api):
    api.set_stage("LEAGUE")
    open_app("knockout")
    _assert_view(page, "knockout")
    tab = stage_tab(page, r"round of 16")
    expect(tab).to_be_visible()
    assert _selected(tab)


def test_stage_api_failure_defaults_to_round_of_16(open_app, stage_tab, page, api):
    api.set_stage("IGNORED", status=500)
    open_app("knockout")
    _assert_view(page, "knockout")
    tab = stage_tab(page, r"round of 16")
    expect(tab).to_be_visible()
    assert _selected(tab)


def test_primary_nav_click_switches_view_and_updates_hash(open_app, page):
    open_app("")
    _assert_view(page, "overview")
    page.locator('[data-view="league"]').first.click()
    expect(page.locator("#league")).to_be_visible()
    expect(page.locator("#overview")).to_be_hidden()
    assert "league" in page.url


def test_browser_back_forward_restores_views(open_app, page):
    open_app("overview")
    page.locator('[data-view="league"]').first.click()
    expect(page.locator("#league")).to_be_visible()

    page.go_back()
    expect(page.locator("#overview")).to_be_visible()
    expect(page.locator("#league")).to_be_hidden()

    page.go_forward()
    expect(page.locator("#league")).to_be_visible()


def test_primary_nav_roving_keyboard_selection(open_app, page):
    open_app("overview")
    first = page.locator('[data-view="overview"]').first
    first.focus()
    page.keyboard.press("ArrowRight")
    assert page.evaluate(
        "() => document.activeElement && document.activeElement.dataset.view"
    ) == "league"
    expect(page.locator("#league")).to_be_visible()


def test_primary_nav_exposes_selected_state(open_app, page):
    open_app("league")
    league_nav = page.locator('[data-view="league"]').first
    assert _selected(league_nav)
    overview_nav = page.locator('[data-view="overview"]').first
    assert not _selected(overview_nav)


def test_stage_tabs_follow_chronological_dom_order(open_app, page):
    """Tabs must be in chronological DOM order (CSS ``order`` is not enough).

    A user scanning or keyboard-navigating the tablist walks the DOM, so the
    source order must be Play-off, Round of 16, Quarter-final, Semi-final, Final.
    """
    open_app("knockout")
    tabs = page.locator("#knockout [role='tab']")
    labels = [
        _normalize_tab_label(tabs.nth(i).inner_text())
        for i in range(tabs.count())
    ]
    assert labels == CHRONOLOGICAL_TAB_LABELS, labels


def test_stage_tabs_have_accessible_associations(open_app, page):
    open_app("knockout")
    tablist = page.locator("#knockout [role='tablist']").first
    expect(tablist).to_be_visible()
    tabs = page.locator("#knockout [role='tab']")
    assert tabs.count() >= 5
    for i in range(tabs.count()):
        tab = tabs.nth(i)
        controls = tab.get_attribute("aria-controls")
        assert controls, "each stage tab must reference its panel via aria-controls"
        expect(page.locator(f"#{controls}")).to_have_count(1)
        assert tab.get_attribute("aria-selected") in ("true", "false")


def test_stage_tabs_roving_keyboard_navigation(open_app, stage_tab, page):
    open_app("knockout")
    tabs = page.locator("#knockout [role='tab']")
    tabs.nth(0).focus()
    page.keyboard.press("ArrowRight")
    active_text = page.evaluate(
        "() => document.activeElement && document.activeElement.textContent"
    )
    assert re.search(r"play-?off|round of 16|group", active_text or "", re.I), active_text
    # Moving right must also select the focused tab.
    assert page.evaluate(
        "() => { const el = document.activeElement; "
        "return el && el.getAttribute('aria-selected') === 'true'; }"
    )


def test_skip_link_preserves_league_view(open_app, page):
    """#main-content is an in-page anchor, never a route."""
    open_app("league")
    expect(page.locator("#league")).to_be_visible()
    skip = page.locator(".skip-link").first
    expect(skip).to_have_attribute("href", "#main-content")
    skip.focus()
    page.keyboard.press("Enter")
    expect(page.locator("#league")).to_be_visible()
    expect(page.locator("#overview")).to_be_hidden()


def test_skip_link_preserves_knockout_view_and_stage(open_app, stage_tab, page):
    open_app("round-of-16")
    _assert_view(page, "knockout")
    tab = stage_tab(page, r"round of 16")
    assert _selected(tab)
    skip = page.locator(".skip-link").first
    skip.focus()
    page.keyboard.press("Enter")
    # Hash is now #main-content; the knockout view and its selected stage must
    # survive the ignored anchor hashchange.
    _assert_view(page, "knockout")
    assert _selected(stage_tab(page, r"round of 16"))


@pytest.mark.parametrize("hash_fragment", ["settings", "v2", "unknown-tab"])
def test_unknown_hash_defaults_to_overview(open_app, page, hash_fragment):
    open_app(hash_fragment)
    _assert_view(page, "overview")
