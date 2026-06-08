"""Normalized, source-agnostic legal-document model.

Every source (CourtListener now; CAP / GovInfo / Congress / eCFR / OpenStates
later) maps its native record into `LegalDoc`, so downstream tools — and the
future retrieval, extraction and synthesis layers — never depend on a single
provider's schema.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(slots=True)
class LegalDoc:
    id: str  # canonical id within the source, e.g. CourtListener cluster "10307218"
    source: str  # "courtlistener", "cap", "govinfo", ...
    doc_type: str = "opinion"  # opinion | statute | regulation | bill | hearing
    title: str = ""  # case name, statute title, etc.
    court: str = ""  # rendering court / issuing body
    court_id: str = ""  # short court code, e.g. "ca1", "scotus"
    jurisdiction: str = ""  # federal | state code | country
    date: str = ""  # primary date (filed / enacted / published), ISO string
    citations: list[str] = field(default_factory=list)  # reporter citations
    docket_number: str = ""
    judges: list[str] = field(default_factory=list)
    status: str = ""  # Published | Unpublished | In force | ...
    summary: str = ""  # snippet / syllabus / preview text
    url: str = ""  # landing page
    download_url: str = ""  # PDF / source document
    cite_count: int = 0  # how many later docs cite this one
    # Source-specific metrics that don't fit the common schema
    # (e.g. date_argued, posture, neutral_cite). Empty for sources without them.
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
