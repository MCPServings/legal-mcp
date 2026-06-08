"""Source registry.

Keeping sources behind a tiny registry (rather than importing one provider
directly in the server) is what makes the "add another jurisdiction" step —
CAP, GovInfo, Congress, eCFR, OpenStates, and the future cross-border rerun —
a config change, not a rewrite.
"""
from __future__ import annotations

import os

from .base import LegalSource
from .courtlistener import CourtListenerSource

_SOURCES: dict[str, LegalSource] = {
    "courtlistener": CourtListenerSource(
        api_token=os.getenv("COURTLISTENER_API_TOKEN")
    ),
}

# Friendly aliases that resolve to a canonical source key.
_ALIASES = {
    "cl": "courtlistener",
    "court-listener": "courtlistener",
    "court_listener": "courtlistener",
    "courtlistener.com": "courtlistener",
}

DEFAULT_SOURCE = "courtlistener"


def get_source(name: str | None = None) -> LegalSource:
    key = (name or DEFAULT_SOURCE).strip().lower()
    key = _ALIASES.get(key, key)
    if key not in _SOURCES:
        raise ValueError(
            f"unknown source {key!r}; available: {', '.join(sorted(_SOURCES))}"
        )
    return _SOURCES[key]


def list_sources() -> list[str]:
    return sorted(_SOURCES)


__all__ = ["LegalSource", "get_source", "list_sources", "DEFAULT_SOURCE"]
