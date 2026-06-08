"""legal-mcp — a remotely callable MCP server for US legal research.

Baseline ("套壳") milestone: a thin, source-agnostic wrapper exposing
CourtListener case-law search/fetch over the MCP streamable-HTTP transport.
It is the skeleton the value layers (multi-jurisdiction aggregation, full-text
retrieval, citation graphs, freshness monitoring) plug into later.
"""

__version__ = "0.1.0"
