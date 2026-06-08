"""FastMCP server exposing US legal search/fetch over streamable HTTP.

Runs standalone at 127.0.0.1:$LEGAL_MCP_PORT with the MCP endpoint at
$LEGAL_MCP_PATH (default ``/mcp``), to be reverse-proxied as a public
streamable-HTTP MCP service.

Baseline tools wrap CourtListener (9M+ US court opinions). The source registry
and the aggregate `search_all` fusion are already in place so additional
jurisdictions (CAP, GovInfo, Congress, eCFR, OpenStates) slot in without a
server rewrite.

Discipline: every tool returns only primary-source text and citation metadata
retrieved from the upstream API. The server never lets a model *generate* legal
content — agents build evidence packs they can check and footnote.
"""
from __future__ import annotations

import asyncio
import os
import re

import httpx
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from . import __version__
from .aggregate import fuse
from .models import LegalDoc
from .sources import DEFAULT_SOURCE, get_source, list_sources

HOST = os.getenv("LEGAL_MCP_HOST", "127.0.0.1")
PORT = int(os.getenv("LEGAL_MCP_PORT", "9500"))
PATH = os.getenv("LEGAL_MCP_PATH", "/mcp")

_INSTRUCTIONS = (
    "US legal research for AI agents (CourtListener baseline: 9M+ federal and "
    "state court opinions, free and public).\n\n"
    "Generic, source-agnostic tools:\n"
    "  1. `search_cases(query=..., court='', max_results=10, sort_by='relevance')`"
    " — full-text case-law search. `court` is an optional CourtListener court "
    "id (e.g. 'scotus', 'ca9', 'cal'); see `list_courts`. sort_by: relevance | "
    "newest | oldest | most_cited.\n"
    "  1b. `search_all(query=..., max_results=10)` searches every configured "
    "corpus at once, de-duplicates the same authority (by reporter citation or "
    "case name) and re-ranks with Reciprocal Rank Fusion. Today that is "
    "CourtListener only; it is the default broad search as more jurisdictions "
    "are added.\n"
    "  2. `get_case(doc_id=..., source='courtlistener')` — one case's full "
    "record by its cluster id.\n"
    "  3. `read_opinion(opinion_id=..., format='text')` — the FULL opinion "
    "text ('text' or 'html'). Opinion ids come from a case's `sub_opinions`.\n"
    "  4. `lookup_citation(text=...)` — resolve reporter citations in free "
    "text (e.g. '576 U.S. 644') to real cases. Needs COURTLISTENER_API_TOKEN.\n"
    "  5. `get_citation_network(doc_id=...)` — the authorities a case relies "
    "on plus how often it is later cited.\n"
    "  6. `list_recent_cases(court=..., max_results=10)` — newest opinions in "
    "a court.\n"
    "  7. `list_courts(source='courtlistener')` — common court ids.\n"
    "  8. `list_legal_sources()` — available corpora.\n\n"
    "Results are normalized across sources, so the same fields apply no matter "
    "which corpus is queried. The server returns retrieved primary sources "
    "only — never model-generated legal text."
)


_TS = TransportSecuritySettings(enable_dns_rebinding_protection=False)
mcp = FastMCP(
    name="legal-search",
    instructions=_INSTRUCTIONS,
    host=HOST,
    port=PORT,
    streamable_http_path=PATH,
    transport_security=_TS,
)

_SUMMARY_PREVIEW = 320

# Corpora fused by `search_all` when the caller doesn't name specific ones.
_AGG_SOURCES = ("courtlistener",)


def _hit(p: LegalDoc) -> dict:
    """Compact form for search hits (summary truncated to save tokens)."""
    summary = p.summary
    if len(summary) > _SUMMARY_PREVIEW:
        summary = summary[:_SUMMARY_PREVIEW].rstrip() + "…"
    hit = {
        "id": p.id,
        "source": p.source,
        "type": p.doc_type,
        "title": p.title,
        "court": p.court,
        "date": p.date,
        "citations": p.citations,
        "docket_number": p.docket_number,
        "cite_count": p.cite_count,
        "status": p.status,
        "url": p.url,
        "summary_preview": summary,
    }
    return {k: v for k, v in hit.items() if v not in ("", [], None)}


@mcp.tool(description="Search US case law (court opinions). Returns normalized "
          "hits with a short text snippet; call get_case for the full record "
          "or read_opinion for full text. Optional `court` filters by "
          "CourtListener court id (see list_courts).")
async def search_cases(
    query: str,
    court: str = "",
    max_results: int = 10,
    start: int = 0,
    sort_by: str = "relevance",
    source: str = DEFAULT_SOURCE,
) -> dict:
    """Search a case-law corpus.

    Args:
        query: Free-text query (party names, topic, judge, phrase).
        court: Optional CourtListener court id (e.g. 'scotus', 'ca9').
        max_results: 1–50.
        start: Offset for pagination.
        sort_by: relevance | newest | oldest | most_cited.
        source: Corpus to search (currently: courtlistener).
    """
    src = get_source(source)
    try:
        docs = await src.search(
            query, max_results=max_results, court=court, start=start, sort_by=sort_by
        )
    except (httpx.HTTPError, ValueError) as exc:
        return {"query": query, "source": source, "error": f"upstream error: {exc}"}
    return {
        "query": query,
        "source": source,
        "court": court,
        "sort_by": sort_by,
        "count": len(docs),
        "results": [_hit(p) for p in docs],
    }


def _merged_hit(group: dict) -> dict:
    """Render one fused group (rep LegalDoc + cross-source meta) as a hit."""
    p: LegalDoc = group["rep"]
    summary = p.summary
    if len(summary) > _SUMMARY_PREVIEW:
        summary = summary[:_SUMMARY_PREVIEW].rstrip() + "…"
    hit = {
        "title": p.title,
        "court": p.court,
        "date": p.date,
        "citations": p.citations,
        "url": p.url,
        "summary_preview": summary,
        "sources": group["sources"],
        "ids": group["ids"],
        "agreement": group["agreement"],
        "score": group["score"],
    }
    if group["cite_count"] is not None:
        hit["cite_count"] = group["cite_count"]
    return {k: v for k, v in hit.items() if v not in ("", [], None)}


@mcp.tool(description="Aggregated case-law search across every configured "
          "corpus at once. Fans out concurrently, de-duplicates the same "
          "authority (by reporter citation or case name) and re-ranks with "
          "Reciprocal Rank Fusion, so cases found by several sources rank "
          "highest. Each hit lists which `sources` found it and an `ids` map "
          "({source: id}) you can pass to get_case. Prefer this over "
          "search_cases for a broad lookup.")
async def search_all(
    query: str,
    max_results: int = 10,
    sources: str = "",
    per_source: int = 0,
) -> dict:
    """Search several corpora at once and return one fused, de-duplicated list.

    Args:
        query: Free-text query.
        max_results: 1–50 merged results to return.
        sources: Comma/space separated corpora to fuse (names or aliases).
            Defaults to all configured sources.
        per_source: How many raw hits to pull from each corpus before fusing.
            0 (default) uses max(max_results, 10) to give the ranker material.
    """
    max_results = max(1, min(max_results, 50))
    wanted = [s for s in re.split(r"[,\s]+", sources.strip()) if s] or list(_AGG_SOURCES)
    fetch_n = per_source if per_source > 0 else max(max_results, 10)

    async def _one(name: str):
        try:
            src = get_source(name)
        except ValueError as exc:
            return name, exc
        try:
            docs = await src.search(query, max_results=fetch_n, sort_by="relevance")
            return src.name, docs
        except (httpx.HTTPError, ValueError) as exc:
            return src.name, exc

    pairs = await asyncio.gather(*(_one(s) for s in wanted))

    results: dict[str, list[LegalDoc]] = {}
    errors: dict[str, str] = {}
    for name, res in pairs:
        if isinstance(res, Exception):
            errors[name] = f"{type(res).__name__}: {res}"
        else:
            results[name] = res

    if not results:
        return {"query": query, "sources_queried": wanted,
                "errors": errors, "count": 0, "results": []}

    fused = fuse(results)
    return {
        "query": query,
        "sources_queried": sorted(results),
        "errors": errors,
        "total_merged": len(fused),
        "count": min(len(fused), max_results),
        "results": [_merged_hit(g) for g in fused[:max_results]],
    }


@mcp.tool(description="Fetch one case (opinion cluster) by id, with citations, "
          "judges and sub-opinion links you can pass to read_opinion.")
async def get_case(doc_id: str, source: str = DEFAULT_SOURCE) -> dict:
    """Fetch a single case's full record by its cluster id."""
    src = get_source(source)
    try:
        doc = await src.fetch(doc_id)
    except (httpx.HTTPError, ValueError) as exc:
        return {"doc_id": doc_id, "source": source, "error": f"upstream error: {exc}"}
    if doc is None:
        return {"doc_id": doc_id, "source": source, "error": "not found"}
    return doc.to_dict()


@mcp.tool(description="Read the FULL text of a single opinion. format='text' "
          "(default, plain-text body) or 'html' (with citation links). Opinion "
          "ids come from a case's `sub_opinions` (see get_case).")
async def read_opinion(
    opinion_id: str, format: str = "text", source: str = DEFAULT_SOURCE
) -> dict:
    """Fetch the full text of one opinion (not just a snippet)."""
    src = get_source(source)
    reader = getattr(src, "read_opinion", None)
    if reader is None:
        return {"opinion_id": opinion_id, "source": source,
                "error": f"source '{source}' does not support full-text reading"}
    try:
        return await reader(opinion_id, fmt=format)
    except ValueError as exc:
        return {"opinion_id": opinion_id, "source": source, "error": str(exc)}
    except httpx.HTTPError as exc:
        return {"opinion_id": opinion_id, "source": source,
                "error": f"upstream error: {exc}"}


@mcp.tool(description="Resolve reporter citations found in free text (e.g. "
          "'410 U.S. 113' or 'Obergefell v. Hodges, 576 U.S. 644') to real "
          "cases. Use BEFORE relying on any citation an agent produced — it "
          "confirms the case exists. Needs COURTLISTENER_API_TOKEN.")
async def lookup_citation(text: str, source: str = DEFAULT_SOURCE) -> dict:
    """Verify/resolve legal citations in free text against the corpus."""
    src = get_source(source)
    fn = getattr(src, "lookup_citation", None)
    if fn is None:
        return {"source": source,
                "error": f"source '{source}' does not support citation lookup"}
    try:
        return await fn(text)
    except httpx.HTTPError as exc:
        return {"source": source, "error": f"upstream error: {exc}"}


@mcp.tool(description="Get the citation network for a case: the authorities it "
          "relies on (outbound) plus how often it is later cited (inbound "
          "count). doc_id is a cluster id (see search_cases / get_case).")
async def get_citation_network(doc_id: str, source: str = DEFAULT_SOURCE) -> dict:
    """Map a case's outbound citations and inbound citation count."""
    src = get_source(source)
    fn = getattr(src, "citation_network", None)
    if fn is None:
        return {"doc_id": doc_id, "source": source,
                "error": f"source '{source}' does not support citation networks"}
    try:
        return await fn(doc_id)
    except (httpx.HTTPError, ValueError) as exc:
        return {"doc_id": doc_id, "source": source, "error": f"upstream error: {exc}"}


@mcp.tool(description="List the newest opinions in a court, most recent first. "
          "court is a CourtListener court id (see list_courts).")
async def list_recent_cases(
    court: str, source: str = DEFAULT_SOURCE, max_results: int = 10, start: int = 0
) -> dict:
    """Latest opinions in a court (e.g. 'scotus')."""
    src = get_source(source)
    try:
        docs = await src.recent(court, max_results=max_results, start=start)
    except (httpx.HTTPError, ValueError) as exc:
        return {"court": court, "source": source, "error": f"upstream error: {exc}"}
    return {
        "court": court,
        "source": source,
        "count": len(docs),
        "results": [_hit(p) for p in docs],
    }


@mcp.tool(description="List common court ids (and their jurisdiction) usable in "
          "search_cases(court=...) and list_recent_cases.")
def list_courts(source: str = DEFAULT_SOURCE) -> dict:
    """Common court ids for filtering case-law search."""
    src = get_source(source)
    return {"source": source, "courts": src.jurisdictions()}


@mcp.tool(description="List available legal corpora.")
def list_legal_sources() -> dict:
    return {"sources": list_sources(), "default": DEFAULT_SOURCE, "version": __version__}


def main() -> None:
    mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
