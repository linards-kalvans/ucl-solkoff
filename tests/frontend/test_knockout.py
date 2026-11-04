"""Knockout view: stage fetching, selection, analysis sidebar and safety."""
from __future__ import annotations

import re

import pytest
from playwright.sync_api import expect

ALPHA = (5, "Alpha Strength FC", "https://crest.example/str.png")
GAMMA = (1, "Gamma Pinnacle", None)
DELTA = (2, "Delta Rovers", "https://crest.example/del.png")
EPSILON = (3, "Epsilon City", "https://crest.example/eps.png")
ETA = (6, "Eta Sporting", "https://crest.example/eta.png")
IOTA = (7, "Iota Athletic", "https://crest.example/iot.png")
Q1 = (21, "Quarter One", "https://crest.example/q1.png")
Q2 = (22, "Quarter Two", "https://crest.example/q2.png")
S1 = (23, "Semi One", "https://crest.example/s1.png")
S2 = (24, "Semi Two", "https://crest.example/s2.png")


@pytest.fixture
def stages(api, build_pair):
    api.set_pairs("ROUND_OF_16", [
        build_pair(1, ALPHA, GAMMA),
        build_pair(2, DELTA, EPSILON),
        build_pair(3, ETA, IOTA),
    ])
    api.set_pairs("QUARTER_FINAL", [build_pair(4, Q1, Q2)])
    api.set_pairs("SEMI_FINAL", [build_pair(5, S1, S2)])
    return api


def _cards(page):
    cards = page.locator("#knockout .match-card")
    if cards.count() == 0:
        cards = page.locator("#knockout .playoff-pair-card")
    return cards


def _analysis_panel(page):
    panel = page.locator("#analysisPanel")
    if panel.count() == 0:
        panel = page.locator("#knockout .analysis-panel")
    return panel.first


def _next_button(page):
    button = page.locator("#knockout [aria-label*='next' i]")
    if button.count() == 0:
        button = page.locator("#knockout .nav-next")
    if button.count() == 0:
        button = page.locator("#knockout button", has_text="›")
    return button.first


def _close_button(page):
    button = page.locator("#knockout [aria-label*='close' i]")
    if button.count() == 0:
        button = page.locator("#knockout .analysis-close, #knockout .modal-close")
    return button.first


def test_selected_stage_pairs_render(stages, open_app, stage_tab, page):
    open_app("knockout")
    expect(stage_tab(page, r"round of 16")).to_be_visible()
    cards = _cards(page)
    expect(cards).to_have_count(3)
    expect(cards.first).to_be_visible()
    text = page.locator("#knockout").inner_text()
    for name in ("Alpha Strength FC", "Delta Rovers", "Eta Sporting"):
        assert name in text


def test_stage_data_is_cached_between_switches(stages, open_app, stage_tab, page, api):
    open_app("knockout")
    stem = r"quarter-?final"
    page.locator("#knockout [role='tab']", has_text=re.compile(stem, re.I)).first.click()
    expect(_cards(page).first).to_contain_text("Quarter One")
    page.locator("#knockout [role='tab']",
                 has_text=re.compile(r"round of 16", re.I)).first.click()
    expect(_cards(page).first).to_contain_text("Alpha Strength FC")

    assert api.counts["GET /api/knockout-pairs/ROUND_OF_16"] == 1
    assert api.counts["GET /api/knockout-pairs/QUARTER_FINAL"] == 1


def test_selecting_card_marks_it_and_opens_sidebar(stages, open_app, page,
                                                   build_analysis):
    open_app("knockout")
    card = _cards(page).filter(has_text="Delta Rovers").first
    card.click()
    selected = (card.get_attribute("aria-selected") == "true"
                or "selected" in (card.get_attribute("class") or "")
                or card.get_attribute("aria-pressed") == "true")
    assert selected
    panel = _analysis_panel(page)
    expect(panel).to_contain_text("Delta Rovers")
    expect(panel).to_contain_text("Epsilon City")


def test_elo_analysis_sections_preserved(stages, open_app, page, build_analysis, api):
    api.set_analysis(DELTA[0], EPSILON[0], build_analysis(DELTA, EPSILON, "elo"),
                     200)
    api.set_analysis(EPSILON[0], DELTA[0], build_analysis(EPSILON, DELTA, "elo"),
                     200)
    open_app("knockout")
    _cards(page).filter(has_text="Delta Rovers").first.click()
    panel = _analysis_panel(page)
    expect(panel).to_contain_text("Elo Rating Breakdown")
    expect(panel).to_contain_text(re.compile(r"win probability", re.I))
    expect(panel).to_contain_text(re.compile(r"1 / \(1 \+ 10", re.I))


def test_historical_analysis_sections_preserved(stages, open_app, page,
                                                build_analysis, api):
    analysis = build_analysis(DELTA, EPSILON, "combined_strength")
    api.set_analysis(DELTA[0], EPSILON[0], analysis, 200)
    api.set_analysis(EPSILON[0], DELTA[0],
                     build_analysis(EPSILON, DELTA, "combined_strength"), 200)
    open_app("knockout")
    _cards(page).filter(has_text="Delta Rovers").first.click()
    panel = _analysis_panel(page)
    expect(panel).to_contain_text(re.compile(r"common opponents", re.I))
    expect(panel).to_contain_text(re.compile(r"full league table", re.I))
    expect(panel).to_contain_text(re.compile(r"match history", re.I))
    expect(panel).to_contain_text(re.compile(r"direct matches", re.I))


def test_probability_labelled_excluding_draws(stages, open_app, page,
                                              build_analysis, api):
    api.set_analysis(DELTA[0], EPSILON[0], build_analysis(DELTA, EPSILON, "elo"), 200)
    api.set_analysis(EPSILON[0], DELTA[0], build_analysis(EPSILON, DELTA, "elo"), 200)
    open_app("knockout")
    _cards(page).filter(has_text="Delta Rovers").first.click()
    expect(_analysis_panel(page)).to_contain_text(re.compile(r"excluding draws", re.I))


def test_previous_next_controls_navigate(stages, open_app, page, build_analysis, api):
    for a, b in ((DELTA, EPSILON), (EPSILON, DELTA), (ALPHA, GAMMA), (GAMMA, ALPHA),
                 (ETA, IOTA), (IOTA, ETA)):
        api.set_analysis(a[0], b[0], build_analysis(a, b, "elo"), 200)
    open_app("knockout")
    middle = _cards(page).nth(1)
    middle.click()
    _next_button(page).click()
    expect(_analysis_panel(page)).to_contain_text("Eta Sporting")


def test_keyboard_navigation_and_escape(stages, open_app, page, build_analysis, api):
    for a, b in ((DELTA, EPSILON), (EPSILON, DELTA), (ALPHA, GAMMA), (GAMMA, ALPHA),
                 (ETA, IOTA), (IOTA, ETA)):
        api.set_analysis(a[0], b[0], build_analysis(a, b, "elo"), 200)
    open_app("knockout")
    _cards(page).nth(1).click()
    page.keyboard.press("ArrowRight")
    expect(_analysis_panel(page)).to_contain_text("Eta Sporting")
    page.keyboard.press("Escape")
    panel = _analysis_panel(page)
    expect(panel).not_to_contain_text("Eta Sporting")


def test_close_button_labelled_and_returns_focus(stages, open_app, page,
                                                 build_analysis, api):
    for a, b in ((DELTA, EPSILON), (EPSILON, DELTA)):
        api.set_analysis(a[0], b[0], build_analysis(a, b, "elo"), 200)
    open_app("knockout")
    card = _cards(page).filter(has_text="Delta Rovers").first
    card.click()
    close = _close_button(page)
    expect(close).to_be_visible()
    assert re.search(r"close", close.get_attribute("aria-label") or "", re.I)
    close.click()
    focused = page.evaluate(
        "() => document.activeElement ? document.activeElement.innerText : ''"
    )
    assert "Delta Rovers" in (focused or "")


def test_stale_stage_response_is_ignored(stages, open_app, page, api, wait_until):
    api.hold_path("/api/knockout-pairs/QUARTER_FINAL")
    open_app("knockout")
    expect(_cards(page).first).to_contain_text("Alpha Strength FC")

    page.locator("#knockout [role='tab']",
                 has_text=re.compile(r"quarter-?final", re.I)).first.click()
    wait_until(lambda: api.held_count >= 1)
    page.locator("#knockout [role='tab']",
                 has_text=re.compile(r"semi-?final", re.I)).first.click()
    expect(_cards(page).first).to_contain_text("Semi One")

    api.release_held()
    # The late quarter-final payload must never replace the newer selection.
    expect(_cards(page).first).to_contain_text("Semi One")
    expect(page.locator("#knockout")).not_to_contain_text("Quarter One")


def test_stale_selected_match_analysis_is_ignored(stages, open_app, page, api,
                                                  build_analysis, wait_until):
    for team1, team2 in ((ALPHA, GAMMA), (GAMMA, ALPHA), (DELTA, EPSILON),
                         (EPSILON, DELTA)):
        api.set_analysis(team1[0], team2[0], build_analysis(team1, team2), 200)
    api.hold_path("/api/playoff-pairs/5/1/analysis")
    open_app("knockout")
    expect(_cards(page).first).to_be_visible()

    _cards(page).filter(has_text="Alpha Strength FC").first.click()
    wait_until(lambda: api.held_count >= 1)
    _cards(page).filter(has_text="Delta Rovers").first.click()
    expect(_analysis_panel(page)).to_contain_text("Epsilon City")

    api.release_held()
    # Alpha/Gamma analysis arriving late must not overwrite Delta/Epsilon.
    expect(_analysis_panel(page)).to_contain_text("Epsilon City")
    expect(_analysis_panel(page)).not_to_contain_text("Gamma Pinnacle")


def test_match_card_keyboard_activation(stages, open_app, page, api, build_analysis):
    for team1, team2 in ((ALPHA, GAMMA), (GAMMA, ALPHA), (DELTA, EPSILON),
                         (EPSILON, DELTA)):
        api.set_analysis(team1[0], team2[0], build_analysis(team1, team2), 200)
    open_app("knockout")
    panel = _analysis_panel(page)

    delta = _cards(page).filter(has_text="Delta Rovers").first
    delta.focus()
    page.keyboard.press("Enter")
    expect(panel).to_contain_text("Epsilon City")

    alpha = _cards(page).filter(has_text="Alpha Strength FC").first
    alpha.focus()
    page.keyboard.press("Space")
    expect(panel).to_contain_text("Gamma Pinnacle")


def test_zero_probability_total_is_unavailable_not_50_50(open_app, page, api,
                                                         build_pair):
    pair = build_pair(1, ALPHA, GAMMA)
    pair["winProbability"] = {"team1Win": 0.0, "team2Win": 0.0, "draw": 0.0,
                               "method": "combined_strength"}
    api.set_pairs("ROUND_OF_16", [pair])
    open_app("knockout")
    card = _cards(page).first
    expect(card).to_be_visible()
    expect(card).to_contain_text(
        re.compile(r"unavailable|no data|insufficient|not available", re.I))
    expect(card).not_to_contain_text(re.compile(r"\b50(\.0)?\s*%"))


def test_loading_state_distinguished(open_app, page, api, build_pair):
    api.set_pairs("ROUND_OF_16", [build_pair(1, ALPHA, GAMMA)])
    api.hold_path("/api/knockout-pairs/ROUND_OF_16")
    open_app("knockout")
    expect(page.locator("#knockout")).to_contain_text(re.compile(r"loading", re.I))
    api.release_held()


def test_empty_stage_distinguished(open_app, page, api):
    api.set_pairs("ROUND_OF_16", [])
    open_app("knockout")
    panel = page.locator("#knockout")
    expect(panel).to_contain_text(
        re.compile(r"no .*(pairs|ties)|not available|unavailable|no data", re.I))
    expect(panel).not_to_contain_text(re.compile(r"error|failed|could not", re.I))


def test_error_stage_distinguished(open_app, page, api):
    api.set_pairs("ROUND_OF_16", [], status=500)
    open_app("knockout")
    expect(page.locator("#knockout")).to_contain_text(
        re.compile(r"error|failed|could not|unavailable", re.I))


def test_missing_and_broken_crests_fall_back_to_initials(open_app, page, api,
                                                         build_pair):
    pair = build_pair(1, GAMMA, (2, "Delta Rovers", "https://crest.example/broken.png"))
    api.set_pairs("ROUND_OF_16", [pair])
    open_app("knockout")
    card = _cards(page).first
    expect(card).to_be_visible()
    # No broken <img> may survive for missing/aborted crests.
    expect(card.locator("img")).to_have_count(0)
    # Real initial fallbacks are rendered for both teams.
    initials = card.locator(".crest, .initials, [class*='placeholder']")
    expect(initials.first).to_be_visible()
    tokens = [initials.nth(i).inner_text().strip() for i in range(initials.count())]
    matched = [token for token in tokens if re.fullmatch(r"[A-Z]{1,3}", token)]
    assert len(matched) >= 2, tokens


def test_unsafe_crest_url_rejected_and_text_inert(open_app, page, api, build_pair):
    evil = (31, '<img src=x onerror="window.__xss=1">Evil FC', "javascript:alert(1)")
    other = (32, "Safe United", "javascript:alert(2)")
    api.set_pairs("ROUND_OF_16", [build_pair(1, evil, other)])
    open_app("knockout")
    panel = page.locator("#knockout")
    # Marker-like name appears literally as text, never parsed as markup.
    expect(panel).to_contain_text("Evil FC")
    expect(panel).to_contain_text("<img")
    assert page.evaluate("() => window.__xss") in (None, False)
    expect(panel.locator("img")).to_have_count(0)
    expect(panel.locator("img[src^='javascript:']")).to_have_count(0)
    # Rejected javascript: crests still fall back to visible initials.
    initials = panel.locator(".crest, .initials, [class*='placeholder']")
    tokens = [initials.nth(i).inner_text().strip() for i in range(initials.count())]
    assert any(re.fullmatch(r"[A-Z]{1,3}", token) for token in tokens), tokens


def test_no_new_inline_handlers(stages, open_app, page):
    open_app("knockout")
    _cards(page).first.click()
    assert page.locator("#knockout [onclick]").count() == 0
