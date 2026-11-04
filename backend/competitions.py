"""Supported European competition registry and code resolution.

The provider (football-data.org v4) exposes the league codes used here:

    CL  -> UEFA Champions League
    EL  -> UEFA Europa League
    UCL -> UEFA Conference League   (note: ``UCL`` is the provider code for the
                                      Conference League, not the app's CL brand)

Source: https://docs.football-data.org/general/v4/lookup_tables.html

This module is deliberately tiny: a registry, a validated configured default and
a request-code resolver. There is no plugin framework.
"""
from __future__ import annotations

import logging
import os
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

DEFAULT_COMPETITION = "CL"

# Provider routing. CL keeps football-data.org; EL/UCL are served by the public
# ESPN JSON endpoints (undocumented; public access does not establish
# unrestricted reuse rights). The ESPN league slug is the path segment used by
# both the site scoreboard and the v2 standings endpoints.
PROVIDER_FOOTBALL_DATA = "football-data"
PROVIDER_ESPN = "espn"

# Ordered registry. ``leagueMatches`` is provider format metadata only (the new
# league-phase game count); Conference League clubs play six league games, the
# other two play eight. It is surfaced to the UI and never used to infer results.
_REGISTRY: "Dict[str, Dict[str, object]]" = {
    "CL": {"code": "CL", "label": "Champions League", "leagueMatches": 8},
    "EL": {"code": "EL", "label": "Europa League", "leagueMatches": 8},
    "UCL": {"code": "UCL", "label": "Conference League", "leagueMatches": 6},
}

# Provider metadata is kept out of ``competitions_metadata`` so the public API
# response is unchanged. Each ESPN competition maps to one league slug.
_PROVIDER: "Dict[str, Dict[str, str]]" = {
    "CL": {"provider": PROVIDER_FOOTBALL_DATA, "espnSlug": ""},
    "EL": {"provider": PROVIDER_ESPN, "espnSlug": "uefa.europa"},
    "UCL": {"provider": PROVIDER_ESPN, "espnSlug": "uefa.europa.conf"},
}

# First season of the UEFA Europa Conference League. Nothing earlier exists, so
# historical backfill must not request pre-inception Conference years.
CONFERENCE_INCEPTION_YEAR = 2021

# First season stored in the new league-phase ("Swiss") format.
FIRST_LEAGUE_PHASE_YEAR = 2024


def supported_codes() -> List[str]:
    """All supported competition codes, in registry order."""
    return list(_REGISTRY)


def competitions_metadata() -> List[Dict[str, object]]:
    """Metadata for every supported competition (fresh copies)."""
    return [dict(_REGISTRY[code]) for code in _REGISTRY]


def provider_for(code: str) -> str:
    """Provider name for a supported competition code (falls back to CL)."""
    entry = _PROVIDER.get(normalize_competition(code))
    return entry["provider"] if entry else PROVIDER_FOOTBALL_DATA


def espn_slug_for(code: str) -> str:
    """ESPN league slug for an ESPN-backed competition.

    Raises ``ValueError`` for a competition without an ESPN slug (e.g. CL), so a
    misrouted call fails loudly instead of requesting an arbitrary league.
    """
    entry = _PROVIDER.get(normalize_competition(code))
    slug = entry["espnSlug"] if entry else ""
    if not slug:
        raise ValueError(f"competition {code!r} has no ESPN slug")
    return slug


def league_matches_for(code: str) -> int:
    """League-phase game count for a competition (8 for CL/EL, 6 for UCL)."""
    entry = _REGISTRY.get(normalize_competition(code))
    return int(entry["leagueMatches"]) if entry else 0


def is_supported(code: object) -> bool:
    """True when ``code`` is a supported competition code (case-insensitive)."""
    return isinstance(code, str) and code.strip().upper() in _REGISTRY


def normalize_competition(code: str) -> str:
    """Upper-case and trim a competition code (no validation)."""
    return str(code).strip().upper()


def configured_competition() -> str:
    """Validated ``COMPETITION_ID`` default.

    A missing, blank or unsupported env value falls back to ``CL`` with a
    warning; it is never propagated as an unknown code.
    """
    raw = os.getenv("COMPETITION_ID", "")
    if is_supported(raw):
        return normalize_competition(raw)
    if raw:
        logger.warning(
            "Unsupported COMPETITION_ID %r; defaulting to %s", raw, DEFAULT_COMPETITION
        )
    return DEFAULT_COMPETITION


def resolve_competition(value: Optional[str]) -> str:
    """Resolve an optional request value to a supported code.

    ``None`` or blank resolves to the configured default. An explicit value is
    validated case-insensitively; an unsupported code raises ``ValueError`` so
    the API layer can return 400 instead of silently falling back.
    """
    if value is None:
        return configured_competition()
    if not isinstance(value, str) or value.strip() == "":
        return configured_competition()
    code = normalize_competition(value)
    if code not in _REGISTRY:
        raise ValueError(code)
    return code


def parse_competition_list(raw: Optional[str], fallback: Optional[List[str]] = None) -> List[str]:
    """Parse a comma-separated competition list (e.g. the ``COMPETITIONS`` env).

    Invalid entries raise ``ValueError`` (fail loud on misconfiguration) and
    duplicates are collapsed while preserving first-seen order. An empty/blank
    value returns ``fallback`` (or the configured default alone).
    """
    if raw is None or raw.strip() == "":
        return list(fallback) if fallback else [configured_competition()]

    resolved: List[str] = []
    for part in raw.split(","):
        token = part.strip()
        if not token:
            continue
        code = normalize_competition(token)
        if code not in _REGISTRY:
            raise ValueError(code)
        if code not in resolved:
            resolved.append(code)
    return resolved or (list(fallback) if fallback else [configured_competition()])
