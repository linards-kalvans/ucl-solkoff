"""Focused regression tests for the final-review acceptance gaps.

Scope is deliberately narrow — three previously-approved gaps only:

1. Refresh / standings failures must be VISIBLE while the active view is
   Overview or Knockout. The legacy ``#error`` element lives inside the
   (hidden) League panel, so a failure reported only there is invisible.

2. A delayed ``/api/tournament/current-stage`` response must not leave the
   visible selected stage out of sync with the URL hash, and a reload of the
   permalink must reopen the same stage.

3. Closing an analysis opened from an Overview preview must leave focus on a
   reachable element of the *active* Knockout view (not on the now-hidden
   Overview control and not lost to ``<body>``).

These are written RED-first: no production code is changed here.
"""
from __future__ import annotations

import pytest
from playwright.sync_api import expect

# (id, name, crest)
ALPHA = (5, "Alpha Strength FC", "https://crest.example/str.png")
GAMMA = (1, "Gamma Pinnacle", None)
Q1 = (21, "Quarter One", "https://crest.example/q1.png")
Q2 = (22, "Quarter Two", "https://crest.example/q2.png")

# Plausible homes for an app-level / inline failure or status message. Tests
# accept any of these so the fix is free to add a global status region.
_STATUS_SELECTORS = [
    "#error", "#status", "#globalStatus", "#global-status", "#appStatus",
    "#app-status", "#refreshStatus", "#refresh-status", "#app-status-message",
    "[role='alert']", "[role='status']", "[aria-live]",
    ".error", ".status", ".app-status", ".global-status", ".refresh-status",
    ".toast",
]

# Public Reload failure copy ("... could not load data ... try again"). Kept
# strict so unrelated visible copy cannot satisfy it.
_REFRESH_FAILURE = r"error|failed|could not|try again|not updated"
# Broader copy for a failed standings load (may read "unavailable").
_LOAD_FAILURE = r"error|failed|could not|unavailable|not available|not updated"

# Returns the text of every VISIBLE element matching one of the status
# selectors whose text matches the failure pattern. ``getBoundingClientRect``
# is zero for anything inside a ``hidden`` / ``display:none`` ancestor, so the
# legacy League-only ``#error`` cannot satisfy this from another view.
_VISIBLE_STATUS_JS = """
(args) => {
    const [selectors, pattern] = args;
    const re = new RegExp(pattern, 'i');
    const found = [];
    for (const selector of selectors) {
        let nodes;
        try { nodes = document.querySelectorAll(selector); } catch (e) { continue; }
        for (const el of nodes) {
            const cs = getComputedStyle(el);
            const rect = el.getBoundingClientRect();
            const visible = cs.display !== 'none' && cs.visibility !== 'hidden'
                && cs.opacity !== '0' && rect.width > 0 && rect.height > 0;
            if (visible && re.test(el.textContent || '')) {
                found.push((el.textContent || '').trim());
            }
        }
    }
    return found;
}
"""


def _visible_status_text(page, pattern):
    return page.evaluate(_VISIBLE_STATUS_JS, [_STATUS_SELECTORS, pattern])


def _selected_stage_tab(page):
    return page.evaluate(
        """() => {
            const tab = document.querySelector('#knockout .tab-btn.active')
                || document.querySelector('#knockout [role="tab"][aria-selected="true"]');
            return tab ? (tab.dataset.tab || null) : null;
        }"""
    )


def _hash_stage(page):
    return (page.evaluate("() => window.location.hash") or "").lstrip("#")


def _drain_released(page, settle_ms=300):
    """Let a manually released held response flow through the app's await chain."""
    try:
        page.wait_for_load_state("networkidle", timeout=1000)
    except Exception:
        pass
    page.wait_for_timeout(settle_ms)


# --------------------------------------------------------------------------- #
# 1. Failures must be visible outside the hidden League panel
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "hash_fragment,active_view",
    [("", "#overview"), ("knockout", "#knockout")],
    ids=["overview", "knockout"],
)
def test_reload_failure_visible_in_active_view(
        open_app, page, api, build_pair, wait_until, hash_fragment, active_view):
    if hash_fragment == "knockout":
        # An elo tie keeps normal card copy free of "unavailable" so only a real
        # failure message can satisfy the assertion.
        api.set_pairs("ROUND_OF_16", [build_pair(1, ALPHA, GAMMA)])
    # Public Reload re-reads already-stored data over GET; fail that read. There
    # is no POST/collection endpoint to fail any more.
    api.set_standings([], status=500)

    open_app(hash_fragment)
    expect(page.locator(active_view)).to_be_visible()

    button = page.locator("#refreshBtn")
    button.click()
    expect(button).to_be_enabled()

    wait_until(lambda: _visible_status_text(page, _REFRESH_FAILURE))
    assert _visible_status_text(page, _REFRESH_FAILURE), (
        f"a failed reload must surface a VISIBLE inline error/status while "
        f"{active_view} is active; it currently only writes the hidden League #error"
    )


def test_initial_standings_failure_visible_on_knockout(
        open_app, page, api, build_pair):
    api.set_pairs("ROUND_OF_16", [build_pair(1, ALPHA, GAMMA)])
    api.set_standings([], status=500)

    open_app("knockout")
    expect(page.locator("#knockout")).to_be_visible()
    # Let the failing standings request resolve/report.
    page.wait_for_timeout(250)

    assert _visible_status_text(page, _LOAD_FAILURE), (
        "a failed initial standings load must be visible on the Knockout view "
        "(e.g. a global status), not only in the hidden League #error"
    )


# --------------------------------------------------------------------------- #
# 2. Delayed current-stage response vs URL hash and reload
# --------------------------------------------------------------------------- #
def test_delayed_current_stage_keeps_hash_and_selected_stage_consistent(
        open_app, page, api, build_pair, wait_until):
    # Current-stage says Quarter-final, pairs exist for both candidate stages.
    api.set_stage("QUARTER_FINAL")
    api.set_pairs("ROUND_OF_16", [build_pair(1, ALPHA, GAMMA)])
    api.set_pairs("QUARTER_FINAL", [build_pair(4, Q1, Q2)])

    api.hold_path("/api/tournament/current-stage")
    open_app("")
    expect(page.locator("#overview")).to_be_visible()

    # User explicitly opens Knockout before the stage API resolves.
    page.locator('[data-view="knockout"]').first.click()
    expect(page.locator("#knockout")).to_be_visible()
    wait_until(lambda: api.held_count >= 1)

    api.release_held()
    _drain_released(page)

    selected = _selected_stage_tab(page)
    hash_stage = _hash_stage(page)
    assert selected, "Knockout must show a selected stage"
    assert selected == hash_stage, (
        f"stage/hash mismatch: hash={hash_stage!r} selected={selected!r}"
    )

    # Reloading the permalink must reopen the exact same stage.
    page.reload(wait_until="domcontentloaded")
    expect(page.locator("#knockout")).to_be_visible()
    wait_until(lambda: _selected_stage_tab(page) is not None)
    assert _selected_stage_tab(page) == selected, (
        f"reload should reopen the {selected!r} stage, not "
        f"{_selected_stage_tab(page)!r}"
    )


# --------------------------------------------------------------------------- #
# 3. Overview-preview close keeps focus on the active view
# --------------------------------------------------------------------------- #
def test_overview_preview_close_keeps_focus_in_active_view(
        open_app, page, api, build_pair, build_analysis):
    # The preview and the Knockout cards share one cached pairs promise. The
    # click handler opens the analysis synchronously, i.e. *before* that cached
    # promise has had a microtask to render the Knockout cards — the reliable
    # reproducer for the "no card to return focus to" case.
    api.set_pairs("ROUND_OF_16", [build_pair(1, ALPHA, GAMMA)])
    analysis = build_analysis(ALPHA, GAMMA)
    api.set_analysis(ALPHA[0], GAMMA[0], analysis)
    api.set_analysis(GAMMA[0], ALPHA[0], analysis)

    open_app("")
    preview = page.locator("#overviewKnockoutCards .mini-match").first
    expect(preview).to_be_visible()
    preview.click()

    expect(page.locator("#knockout")).to_be_visible()
    panel = page.locator("#analysisPanel")
    expect(panel).to_be_visible()

    close = page.locator("#analysisPanel .analysis-close")
    expect(close).to_be_visible()
    close.click()
    expect(panel).to_be_hidden()
    page.wait_for_timeout(50)

    focused = page.evaluate(
        """() => {
            const el = document.activeElement;
            if (!el || el === document.body || el === document.documentElement) {
                return null;
            }
            const cs = getComputedStyle(el);
            const rect = el.getBoundingClientRect();
            return {
                tag: el.tagName,
                inKnockout: !!el.closest('#knockout'),
                visible: cs.display !== 'none' && cs.visibility !== 'hidden'
                    && cs.opacity !== '0' && rect.width > 0 && rect.height > 0,
            };
        }"""
    )
    assert focused is not None, (
        "closing the sidebar must not drop focus to <body>"
    )
    assert focused["inKnockout"], (
        "focus must stay on the active Knockout view (e.g. the stage tab), never "
        "a hidden Overview preview control"
    )
    assert focused["visible"], "the focused element must be visible"
