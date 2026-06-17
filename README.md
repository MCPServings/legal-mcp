# legal-mcp

A remotely callable **MCP server for US legal research**. Baseline ("套壳")
milestone: a thin, source-agnostic wrapper over **CourtListener** (the Free Law
Project's 9M+ federal and state court opinions — free and public), exposed over
the MCP streamable-HTTP transport.

It is deliberately the *skeleton* the value layers plug into later
(multi-jurisdiction aggregation, full-text retrieval, citation graphs,
freshness monitoring), mirroring the architecture of `paper-mcp`.

## Design discipline

The server returns **only primary-source text and citation metadata retrieved
from the upstream API**. It never lets a model *generate* legal content — agents
build evidence packs they can check and footnote. (Legal hallucination has real
sanctions; the server refuses to be the source of one.)

## Tools

| Tool | What it does |
|---|---|
| `search_cases(query, court='', sort_by='relevance')` | Full-text case-law search. |
| `search_all(query)` | Aggregated search across every configured corpus, de-duplicated and re-ranked with Reciprocal Rank Fusion. |
| `get_case(doc_id)` | One case's full record (citations, judges, sub-opinion links). |
| `read_opinion(opinion_id, format='text')` | Full opinion text (`text` or `html`). |
| `lookup_citation(text)` | Resolve reporter citations in free text to real cases. Needs a token. |
| `get_citation_network(doc_id)` | Authorities a case relies on + how often it is later cited. |
| `list_recent_cases(court)` | Newest opinions in a court. |
| `list_courts()` | Common court ids. |
| `list_legal_sources()` | Available corpora. |

## Architecture

```
legal_mcp/
├── __init__.py            version string
├── models.py              LegalDoc — normalized, source-agnostic record
├── aggregate.py           pure RRF fusion + de-dup (by citation / case name)
├── server.py              FastMCP bootstrap + tool registrations
└── sources/
    ├── base.py            LegalSource Protocol (the swappable contract)
    ├── __init__.py        source registry + aliases
    └── courtlistener.py   CourtListener REST client → LegalDoc
```

Adding a jurisdiction (CAP, GovInfo, Congress, eCFR, OpenStates) is a new
`sources/<name>.py` implementing the `LegalSource` contract plus one line in the
registry — no server change.

## Configuration

| Env var | Default | Purpose |
|---|---|---|
| `LEGAL_MCP_HOST` | `127.0.0.1` | bind host |
| `LEGAL_MCP_PORT` | `9500` | bind port |
| `LEGAL_MCP_PATH` | `/mcp` | MCP endpoint path |
| `COURTLISTENER_API_TOKEN` | — | optional; raises rate limits and unlocks `lookup_citation` |

## Run

```bash
pip install -e .
legal-mcp                 # serves streamable-HTTP at 127.0.0.1:9500/mcp
```

Anonymous access already allows search and document fetch (rate-limited). Set
`COURTLISTENER_API_TOKEN` for higher limits and citation lookup.

## Roadmap

1. **MVP (this)** — CourtListener case law: search, full text, citations.
2. Add federal sources — eCFR (regulations), GovInfo (US Code), Congress
   (bills/votes/members), Federal Register, 26 USC/CFR (tax).
3. Add CAP (Harvard) historical 50-state case law, fused with CourtListener.
4. Add OpenStates (50-state legislation) for the state layer UK has no analogue
   for.
5. Freshness pipeline — incremental watermark crawl + embeddings (slow data:
   ~hundreds of new docs/day, &lt;10 min GPU/day).

---

## License

MIT © MCPServings. See [LICENSE](LICENSE).
