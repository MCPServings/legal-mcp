"""The swappable source contract.

A `LegalSource` knows how to search a corpus and fetch a single record,
returning normalized `LegalDoc` objects. The MCP server talks only to this
interface, never to a provider's raw API — so adding a jurisdiction (or, later,
putting a semantic-retrieval brain in front of one) needs no server change.

Optional capabilities (full-text reading, citation resolution, citation
networks) are discovered with ``getattr`` on the concrete source, so a source
only implements what its upstream supports.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..models import LegalDoc


@runtime_checkable
class LegalSource(Protocol):
    name: str

    async def search(
        self,
        query: str,
        *,
        max_results: int = 10,
        court: str = "",
        start: int = 0,
        sort_by: str = "relevance",
    ) -> list[LegalDoc]: ...

    async def fetch(self, doc_id: str) -> LegalDoc | None: ...

    async def recent(
        self, court: str, *, max_results: int = 10, start: int = 0
    ) -> list[LegalDoc]: ...

    def jurisdictions(self) -> list[dict]: ...
