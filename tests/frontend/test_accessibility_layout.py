"""Accessibility, responsive layout and motion-reduction acceptance checks."""
from __future__ import annotations

import re
import urllib.request

import pytest
from playwright.sync_api import expect


def _horizontal_overflow(page):
    return page.evaluate(
        """() => {
            const doc = document.documentElement;
            const body = document.body;
            return {
                doc: doc.scrollWidth - doc.clientWidth,
                body: body ? body.scrollWidth - body.clientWidth : 0,
            };
        }"""
    )


def _ancestor_overflow_x(page, selector):
    return page.locator(selector).first.evaluate(
        """el => {
            let node = el.parentElement;
            while (node) {
                const style = getComputedStyle(node);
                if (['auto', 'scroll'].includes(style.overflowX)) {
                    return {
                        overflowX: style.overflowX,
                        scrollable: node.scrollWidth > node.clientWidth,
                    };
                }
                node = node.parentElement;
            }
            return null;
        }"""
    )


@pytest.mark.parametrize("width", [375, 768, 1440])
def test_no_page_wide_horizontal_overflow(open_app, page, width):
    page.set_viewport_size({"width": width, "height": 900})
    open_app("")
    expect(page.locator("#overview")).to_be_visible()
    overflow = _horizontal_overflow(page)
    assert overflow["doc"] <= 1, overflow
    assert overflow["body"] <= 1, overflow


@pytest.mark.parametrize("width", [375, 768, 1440])
def test_league_table_scrolls_internally(open_app, page, width):
    page.set_viewport_size({"width": width, "height": 900})
    open_app("league")
    expect(page.locator("#league")).to_be_visible()
    expect(page.locator("#standingsTable")).to_be_visible()
    overflow = _horizontal_overflow(page)
    assert overflow["doc"] <= 1, overflow
    assert overflow["body"] <= 1, overflow
    wrapper = _ancestor_overflow_x(page, "#standingsTable")
    assert wrapper is not None, "league table needs an internally scrolling wrapper"


def test_stage_strip_scrolls_internally_at_375(open_app, page):
    page.set_viewport_size({"width": 375, "height": 900})
    open_app("knockout")
    strip = page.locator("#knockout [role='tablist']").first
    expect(strip).to_be_visible()
    overflow = _horizontal_overflow(page)
    assert overflow["doc"] <= 1, overflow
    style = strip.evaluate("el => getComputedStyle(el).overflowX")
    assert style in ("auto", "scroll")


def test_refresh_and_timestamp_visible_at_375(open_app, page):
    page.set_viewport_size({"width": 375, "height": 900})
    open_app("league")
    expect(page.locator("#league")).to_be_visible()
    expect(page.locator("#refreshBtn")).to_be_visible()
    expect(page.locator("#lastUpdated")).to_be_visible()


def test_stylesheet_has_reduced_motion_and_focus(open_app, page, static_server):
    open_app("")
    expect(page.locator("#overview")).to_be_visible()
    css = urllib.request.urlopen(static_server + "/styles.css").read().decode()
    assert "prefers-reduced-motion" in css
    assert "focus-visible" in css


def test_analysis_uses_sidebar_without_modal_or_body_lock(open_app, page, api,
                                                          build_pair, build_analysis):
    pair = build_pair(1, (5, "Alpha Strength FC", None), (1, "Gamma Pinnacle", None),
                      method="combined_strength")
    api.set_pairs("ROUND_OF_16", [pair])
    analysis = build_analysis((5, "Alpha Strength FC", None),
                              (1, "Gamma Pinnacle", None), "combined_strength")
    api.set_analysis(5, 1, analysis)
    api.set_analysis(1, 5, analysis)
    open_app("knockout")
    page.locator("#knockout .match-card, #knockout .playoff-pair-card").first.click()
    expect(page.locator("#knockout")).to_contain_text("Alpha Strength FC")

    assert page.locator(".modal-overlay").count() == 0
    body_overflow = page.evaluate("() => getComputedStyle(document.body).overflowY")
    assert body_overflow not in ("hidden", "clip"), body_overflow
    # Analysis must be present inline in the knockout view (sidebar / stacked).
    panel = page.locator("#analysisPanel")
    if panel.count() == 0:
        panel = page.locator("#knockout .analysis-panel")
    expect(panel.first).to_be_visible()


@pytest.mark.parametrize("width", [375, 1440])
def test_detailed_analysis_tables_scroll_internally(open_app, page, api, build_pair,
                                                    build_analysis, width):
    page.set_viewport_size({"width": width, "height": 900})
    pair = build_pair(1, (5, "Alpha Strength FC", None), (1, "Gamma Pinnacle", None),
                      method="combined_strength")
    api.set_pairs("ROUND_OF_16", [pair])
    analysis = build_analysis((5, "Alpha Strength FC", None),
                              (1, "Gamma Pinnacle", None), "combined_strength")
    api.set_analysis(5, 1, analysis)
    api.set_analysis(1, 5, analysis)
    open_app("knockout")
    page.locator("#knockout .match-card, #knockout .playoff-pair-card").first.click()
    expect(page.locator("#knockout")).to_contain_text("Full League Table")

    overflow = _horizontal_overflow(page)
    assert overflow["doc"] <= 1, overflow
    assert overflow["body"] <= 1, overflow
    table = page.locator("#knockout table").first
    wrapper = _ancestor_overflow_x(page, "#knockout table")
    assert wrapper is not None, "analysis tables need internal scrolling"


def test_refresh_button_has_accessible_name(open_app, page):
    open_app("")
    expect(page.locator("#overview")).to_be_visible()
    button = page.locator("#refreshBtn")
    expect(button).to_be_visible()
    label = button.inner_text().strip() or button.get_attribute("aria-label") or ""
    assert re.search(r"reload", label, re.I), label
