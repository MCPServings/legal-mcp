"""CourtListener source — a thin client over the public CourtListener REST API.

This is a pure pass-through wrapper (no corpus of our own, no index): it
forwards queries to https://www.courtlistener.com/api/rest/v4/ and normalizes
the JSON into `LegalDoc`. It is the baseline skeleton the value layers plug
into later, not a moat in itself.

Auth is optional: anonymous access already allows search and document fetch
(rate-limited). Setting ``COURTLISTENER_API_TOKEN`` raises the rate limit and
unlocks the citation-lookup endpoint.

Covers the Free Law Project's 9M+ US court opinions (federal + state appellate)
plus the RECAP docket archive. All data is free and public.
"""
from __future__ import annotations

import asyncio

import httpx

from ..models import LegalDoc

CL_API = "https://www.courtlistener.com/api/rest/v4"
CL_WEB = "https://www.courtlistener.com"
_USER_AGENT = "legal-mcp/0.1 (+https://github.com/legal-mcp)"
_RETRY_STATUS = {429, 500, 502, 503, 504}

# CourtListener search "order_by" values keyed by our generic sort terms.
_SORT_MAP = {
    "relevance": "score desc",
    "newest": "dateFiled desc",
    "oldest": "dateFiled asc",
    "most_cited": "citeCount desc",
}

# Agent-relevant subset of CourtListener court ids (any valid id works in
# `court=`). Not exhaustive — see list_courts for the live roster.
_COURTS = (
    {"id": "scotus", "name": "Supreme Court of the United States", "jurisdiction": "federal"},
    {"id": "ca1", "name": "Court of Appeals for the First Circuit", "jurisdiction": "federal"},
    {"id": "ca2", "name": "Court of Appeals for the Second Circuit", "jurisdiction": "federal"},
    {"id": "ca3", "name": "Court of Appeals for the Third Circuit", "jurisdiction": "federal"},
    {"id": "ca4", "name": "Court of Appeals for the Fourth Circuit", "jurisdiction": "federal"},
    {"id": "ca5", "name": "Court of Appeals for the Fifth Circuit", "jurisdiction": "federal"},
    {"id": "ca6", "name": "Court of Appeals for the Sixth Circuit", "jurisdiction": "federal"},
    {"id": "ca7", "name": "Court of Appeals for the Seventh Circuit", "jurisdiction": "federal"},
    {"id": "ca8", "name": "Court of Appeals for the Eighth Circuit", "jurisdiction": "federal"},
    {"id": "ca9", "name": "Court of Appeals for the Ninth Circuit", "jurisdiction": "federal"},
    {"id": "ca10", "name": "Court of Appeals for the Tenth Circuit", "jurisdiction": "federal"},
    {"id": "ca11", "name": "Court of Appeals for the Eleventh Circuit", "jurisdiction": "federal"},
    {"id": "cadc", "name": "Court of Appeals for the D.C. Circuit", "jurisdiction": "federal"},
    {"id": "cafc", "name": "Court of Appeals for the Federal Circuit", "jurisdiction": "federal"},
    {"id": "cal", "name": "Supreme Court of California", "jurisdiction": "state:cal"},
    {"id": "ny", "name": "New York Court of Appeals", "jurisdiction": "state:ny"},
    {"id": "tex", "name": "Supreme Court of Texas", "jurisdiction": "state:tex"},
)


class CourtListenerSource:
    name = "courtlistener"

    def __init__(
        self,
        *,
        api_token: str | None = None,
        base_url: str = CL_API,
        timeout: float = 20.0,
        max_retries: int = 4,
    ) -> None:
        self._token = (api_token or "").strip() or None
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._max_retries = max_retries

    # -- public LegalSource interface ---------------------------------------

    async def search(
        self,
        query: str,
        *,
        max_results: int = 10,
        court: str = "",
        start: int = 0,
        sort_by: str = "relevance",
    ) -> list[LegalDoc]:
        q = query.strip()
        if not q:
            raise ValueError("query must not be empty")
        params = {
            "q": q,
            "type": "o",  # opinions / case law
            "order_by": _SORT_MAP.get(sort_by, "score desc"),
            "page_size": max(1, min(max_results, 50)),
        }
        if court.strip():
            params["court"] = court.strip()
        if start > 0:
            params["page"] = max(1, start // max(1, max_results) + 1)
        data = await self._get("/search/", params)
        # CourtListener's search endpoint returns a fixed page (~20) and ignores
        # small page_size values, so honor max_results ourselves.
        rows = (data.get("results") or [])[: max(1, min(max_results, 50))]
        return [self._parse_result(r) for r in rows]

    async def fetch(self, doc_id: str) -> LegalDoc | None:
        """Fetch one opinion cluster (a case) by its cluster id."""
        cid = doc_id.strip().rsplit("/", 1)[-1]
        if not cid:
            raise ValueError("doc_id must not be empty")
        data = await self._get(f"/clusters/{cid}/", {})
        if not data or "id" not in data:
            return None
        return self._parse_cluster(data)

    async def recent(
        self, court: str, *, max_results: int = 10, start: int = 0
    ) -> list[LegalDoc]:
        c = court.strip()
        if not c:
            raise ValueError("court must not be empty")
        return await self.search(
            "*", max_results=max_results, court=c, start=start, sort_by="newest"
        )

    def jurisdictions(self) -> list[dict]:
        """Common CourtListener court ids for use in `court=` filters.

        Not exhaustive — any valid court id works in search; this is the
        agent-relevant subset (SCOTUS + the federal circuits + a few states).
        Call list_courts for the live roster.
        """
        return list(_COURTS)

    # -- optional capabilities (discovered via getattr in the server) -------

    async def read_opinion(self, opinion_id: str, *, fmt: str = "text") -> dict:
        """Return the full text of a single opinion.

        fmt:
          * ``text`` (default) — plain-text body.
          * ``html`` — the source HTML (with citation links when available).
        """
        oid = opinion_id.strip().rsplit("/", 1)[-1]
        if not oid:
            raise ValueError("opinion_id must not be empty")
        fmt = (fmt or "text").strip().lower()
        if fmt not in ("text", "html"):
            raise ValueError("fmt must be 'text' or 'html'")
        data = await self._get(f"/opinions/{oid}/", {})
        if not data or "id" not in data:
            return {"opinion_id": oid, "error": "not found"}
        if fmt == "html":
            body = data.get("html_with_citations") or data.get("html") or ""
        else:
            body = data.get("plain_text") or ""
        if not body and data.get("download_url"):
            return {"opinion_id": oid, "format": fmt, "content": "",
                    "download_url": data.get("download_url"),
                    "note": "no inline text; original document at download_url"}
        return {
            "opinion_id": oid,
            "format": fmt,
            "type": data.get("type"),
            "author": data.get("author_str") or "",
            "download_url": data.get("download_url") or "",
            "length": len(body),
            "content": body,
        }

    async def lookup_citation(self, text: str) -> dict:
        """Resolve reporter citations found in free text to CourtListener cases.

        Requires ``COURTLISTENER_API_TOKEN`` (the citation-lookup endpoint is
        authenticated). Returns each citation with its resolved cluster(s).
        """
        if not self._token:
            return {"error": "citation lookup requires COURTLISTENER_API_TOKEN"}
        payload = {"text": text.strip()}
        data = await self._post("/citation-lookup/", payload)
        return {"citations": data if isinstance(data, list) else data}

    async def citation_network(self, doc_id: str) -> dict:
        """Return the citation network for an opinion cluster.

        ``cited`` are authorities the opinion relies on; ``cite_count`` is how
        many later opinions cite it (forward graph size).
        """
        cid = doc_id.strip().rsplit("/", 1)[-1]
        cluster = await self._get(f"/clusters/{cid}/", {})
        if not cluster or "id" not in cluster:
            return {"doc_id": cid, "error": "not found"}
        sub = cluster.get("sub_opinions") or []
        cited_ids: list[int] = []
        for op_url in sub:
            oid = str(op_url).rstrip("/").rsplit("/", 1)[-1]
            op = await self._get(f"/opinions/{oid}/", {})
            for c in (op.get("opinions_cited") or []):
                ref = str(c).rstrip("/").rsplit("/", 1)[-1]
                if ref.isdigit():
                    cited_ids.append(int(ref))
        return {
            "doc_id": cid,
            "case": cluster.get("case_name"),
            "cites_out": len(cited_ids),
            "cited_opinion_ids": cited_ids[:100],
            "citation_string": (cluster.get("citations") or None),
        }

    # -- parsing ------------------------------------------------------------

    def _parse_result(self, r: dict) -> LegalDoc:
        """Normalize a /search/ result row (type=o) into a LegalDoc."""
        abs_url = r.get("absolute_url") or ""
        opinions = r.get("opinions") or []
        snippet = ""
        if opinions and isinstance(opinions[0], dict):
            snippet = (opinions[0].get("snippet") or "").strip()
        return LegalDoc(
            id=str(r.get("cluster_id") or ""),
            source=self.name,
            doc_type="opinion",
            title=r.get("caseName") or r.get("caseNameFull") or "",
            court=r.get("court") or "",
            court_id=r.get("court_id") or "",
            jurisdiction=r.get("court_jurisdiction") or "",
            date=(r.get("dateFiled") or "")[:10],
            citations=list(r.get("citation") or []),
            docket_number=r.get("docketNumber") or "",
            judges=[r["judge"]] if r.get("judge") else [],
            status=r.get("status") or "",
            summary=snippet,
            url=f"{CL_WEB}{abs_url}" if abs_url else "",
            cite_count=r.get("citeCount") or 0,
            extra={
                "docket_id": r.get("docket_id"),
                "date_argued": r.get("dateArgued"),
                "neutral_cite": r.get("neutralCite") or "",
            },
        )

    def _parse_cluster(self, c: dict) -> LegalDoc:
        """Normalize a /clusters/{id}/ detail object into a LegalDoc."""
        abs_url = c.get("absolute_url") or ""
        cites = [
            f"{ct.get('volume','')} {ct.get('reporter','')} {ct.get('page','')}".strip()
            for ct in (c.get("citations") or [])
            if isinstance(ct, dict)
        ]
        judges = []
        if c.get("judges"):
            judges = [j.strip() for j in str(c["judges"]).split(",") if j.strip()]
        return LegalDoc(
            id=str(c.get("id") or ""),
            source=self.name,
            doc_type="opinion",
            title=c.get("case_name") or c.get("case_name_full") or "",
            date=(c.get("date_filed") or "")[:10],
            citations=cites,
            judges=judges,
            status=c.get("precedential_status") or "",
            summary=(c.get("syllabus") or c.get("summary") or "").strip(),
            url=f"{CL_WEB}{abs_url}" if abs_url else "",
            cite_count=c.get("citation_count") or 0,
            extra={
                "docket": c.get("docket"),
                "sub_opinions": c.get("sub_opinions") or [],
                "date_argued": c.get("date_argued"),
                "scdb_id": c.get("scdb_id") or "",
            },
        )

    # -- HTTP (same retry/backoff policy as the paper-mcp arxiv client) ------

    def _headers(self) -> dict:
        h = {"User-Agent": _USER_AGENT, "Accept": "application/json"}
        if self._token:
            h["Authorization"] = f"Token {self._token}"
        return h

    async def _get(self, path: str, params: dict) -> dict:
        url = f"{self._base_url}{path}"
        last_exc: Exception | None = None
        async with httpx.AsyncClient(
            timeout=self._timeout, follow_redirects=True, headers=self._headers()
        ) as client:
            for attempt in range(self._max_retries):
                backoff = min(3.0 * (attempt + 1), 12.0)
                try:
                    resp = await client.get(url, params=params)
                except httpx.TransportError as exc:
                    last_exc = exc
                    await asyncio.sleep(backoff)
                    continue
                if resp.status_code == 404:
                    return {}
                if resp.status_code in (401, 403):
                    raise ValueError(
                        "this CourtListener endpoint requires authentication; "
                        "set COURTLISTENER_API_TOKEN (free) to use it"
                    )
                if resp.status_code in _RETRY_STATUS:
                    last_exc = httpx.HTTPStatusError(
                        f"CourtListener returned {resp.status_code}",
                        request=resp.request, response=resp)
                    await asyncio.sleep(backoff)
                    continue
                resp.raise_for_status()
                return resp.json()
        assert last_exc is not None
        raise last_exc

    async def _post(self, path: str, payload: dict) -> dict | list:
        url = f"{self._base_url}{path}"
        last_exc: Exception | None = None
        async with httpx.AsyncClient(
            timeout=self._timeout, follow_redirects=True, headers=self._headers()
        ) as client:
            for attempt in range(self._max_retries):
                backoff = min(3.0 * (attempt + 1), 12.0)
                try:
                    resp = await client.post(url, json=payload)
                except httpx.TransportError as exc:
                    last_exc = exc
                    await asyncio.sleep(backoff)
                    continue
                if resp.status_code in (401, 403):
                    raise ValueError(
                        "this CourtListener endpoint requires authentication; "
                        "set COURTLISTENER_API_TOKEN (free) to use it"
                    )
                if resp.status_code in _RETRY_STATUS:
                    last_exc = httpx.HTTPStatusError(
                        f"CourtListener returned {resp.status_code}",
                        request=resp.request, response=resp)
                    await asyncio.sleep(backoff)
                    continue
                resp.raise_for_status()
                return resp.json()
        assert last_exc is not None
        raise last_exc
