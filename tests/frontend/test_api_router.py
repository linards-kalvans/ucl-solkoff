"""Regression tests for the frontend ``ApiRouter`` test fixture itself.

These exercise the in-memory API router directly -- no browser, no static
server -- so fixture-side routing regressions fail loudly at the unit level.
Regression guarded here: the ``/api/knockout-pairs/<stage>`` path regex once
excluded digits, so the real ``ROUND_OF_16`` stage fell through to the generic
``{}`` fallback instead of returning the configured pair list.
"""
from __future__ import annotations

import json

ALPHA = (5, "Alpha Strength FC", "https://crest.example/str.png")
GAMMA = (1, "Gamma Pinnacle", None)


class _FakeRequest:
    def __init__(self, url, method="GET"):
        self.url = url
        self.method = method


class _FakeRoute:
    """Minimal stand-in for a Playwright route, capturing the fulfilment."""

    def __init__(self, url, method="GET"):
        self.request = _FakeRequest(url, method)
        self.fulfilled = None

    def fulfill(self, status, content_type, body):
        self.fulfilled = (status, content_type, body)


def test_configured_round_of_16_pairs_are_returned(api, build_pair):
    """A configured digit-bearing stage must be served, not the {} fallback."""
    pair = build_pair(1, ALPHA, GAMMA)
    api.set_pairs("ROUND_OF_16", [pair])

    route = _FakeRoute("http://127.0.0.1/api/knockout-pairs/ROUND_OF_16")
    api.route_request(route)

    assert route.fulfilled is not None, "router never fulfilled the request"
    status, _content_type, body = route.fulfilled
    assert status == 200
    assert json.loads(body) == [pair]
