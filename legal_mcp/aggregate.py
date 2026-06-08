"""Cross-source result fusion for the aggregated ``search_all`` tool.

Pure functions only — they take the per-source ranked ``LegalDoc`` lists the
server has already fetched and merge them into one de-duplicated, re-ranked
list. No network, no I/O, so the ranking/merge logic is unit-testable on its
own.

De-duplication
    Two records are the same authority when they share a normalized reporter
    citation (e.g. ``576 U.S. 644``) or, lacking one, a normalized case title.
    The same opinion is often reproduced across corpora (CourtListener, CAP,
    Google Scholar) under different ids, so the citation/title fallback is what
    links them.

Ranking
    Reciprocal Rank Fusion (RRF) over each source's own relevance order — the
    standard way to combine ranked lists whose raw scores are not comparable.
    A case surfaced by several corpora accumulates rank contributions from each
    and therefore rises above one found by a single corpus. Citation count
    (how often a case is later cited) breaks ties only.
"""
from __future__ import annotations

import re

from .models import LegalDoc

# Standard RRF damping constant; larger flattens the contribution of top ranks.
RRF_K = 60

_NON_ALNUM = re.compile(r"[^a-z0-9]+")
_WS = re.compile(r"\s+")


def _norm_title(title: str) -> str:
    """Lowercase and collapse every run of non-alphanumerics to one space."""
    return _NON_ALNUM.sub(" ", (title or "").lower()).strip()


def _norm_citation(cite: str) -> str:
    """Normalize a reporter citation for matching (collapse spacing/case)."""
    return _WS.sub(" ", (cite or "").strip().lower())


def _first_citation(p: LegalDoc) -> str | None:
    for c in p.citations:
        n = _norm_citation(c)
        if n:
            return n
    return None


def _richer(a: LegalDoc, b: LegalDoc) -> LegalDoc:
    """Pick the better representative of a duplicate pair.

    Prefer the record with the longer summary, then one carrying a downloadable
    document, then one carrying a reporter citation. This keeps the merged hit
    as informative as the richest single source allowed.
    """
    if len(a.summary) != len(b.summary):
        return a if len(a.summary) > len(b.summary) else b
    if bool(a.download_url) != bool(b.download_url):
        return a if a.download_url else b
    if bool(a.citations) != bool(b.citations):
        return a if a.citations else b
    return a


def fuse(results_by_source: dict[str, list[LegalDoc]]) -> list[dict]:
    """Merge per-source ranked ``LegalDoc`` lists into one fused, ranked list.

    Args:
        results_by_source: ``{canonical_source_name: [LegalDoc, ...]}`` where
            each list is in that source's own relevance order (rank 0 = best).

    Returns:
        A list of merged-group dicts, best first. Each has:
        ``rep`` (the richest ``LegalDoc``), ``sources`` (sorted names that found
        it), ``ids`` (``{source: native_id}`` for follow-up calls),
        ``cite_count`` (max seen), ``agreement`` (number of sources), and
        ``score`` (RRF, rounded).
    """
    groups: list[dict] = []
    by_cite: dict[str, dict] = {}
    by_title: dict[str, dict] = {}

    for source, docs in results_by_source.items():
        for rank, p in enumerate(docs):
            cite = _first_citation(p)
            title = _norm_title(p.title) or None
            group = None
            if cite is not None:
                group = by_cite.get(cite)
            if group is None and title is not None:
                group = by_title.get(title)
            if group is None:
                group = {
                    "rep": p,
                    "sources": set(),
                    "ids": {},
                    "cites": None,
                    "rrf": 0.0,
                }
                groups.append(group)

            group["rep"] = _richer(group["rep"], p)
            group["sources"].add(source)
            group["ids"].setdefault(source, p.id)
            group["rrf"] += 1.0 / (RRF_K + rank)
            cc = p.cite_count if isinstance(p.cite_count, int) else None
            if cc is not None:
                group["cites"] = cc if group["cites"] is None else max(group["cites"], cc)

            # Register both keys so a later record sharing either one joins this
            # group (links a citation-bearing record to a citation-less same-title one).
            if cite is not None:
                by_cite.setdefault(cite, group)
            if title is not None:
                by_title.setdefault(title, group)

    groups.sort(key=lambda g: (g["rrf"], g["cites"] or 0), reverse=True)

    out: list[dict] = []
    for g in groups:
        out.append(
            {
                "rep": g["rep"],
                "sources": sorted(g["sources"]),
                "ids": g["ids"],
                "cite_count": g["cites"],
                "agreement": len(g["sources"]),
                "score": round(g["rrf"], 5),
            }
        )
    return out
