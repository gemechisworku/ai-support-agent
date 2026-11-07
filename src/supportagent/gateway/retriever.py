"""Policy retrieval for `search_policy` (GW-R7), behind a protocol so unit tests use a fake.

`KbRetriever` runs the KB library's hybrid search in-process (gateway → kb is an allowed import;
specs/README.md). The KB code is synchronous SQLAlchemy, so each search runs in a worker thread with
a session from `sessions()` (by default a short-lived one per search). The confidence threshold is
the KB's (`KB_MIN_SIMILARITY`) unless `min_similarity` is given — e.g.
`kb.retrieval.HASH_MIN_SIMILARITY` with the fake embedder.
"""

import asyncio
from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import Protocol

from sqlalchemy import Engine
from sqlalchemy.orm import Session

from supportagent.contracts.kb import PolicyQuery, PolicySearchResult
from supportagent.kb.embed import Embedder
from supportagent.kb.retrieval import search


class Retriever(Protocol):
    async def search(self, query: PolicyQuery) -> PolicySearchResult: ...


class KbRetriever:
    def __init__(
        self,
        sessions: Callable[[], AbstractContextManager[Session]],
        embedder: Embedder,
        min_similarity: float | None = None,
    ) -> None:
        self._sessions = sessions
        self._embedder = embedder
        self._min_similarity = min_similarity

    @classmethod
    def from_engine(
        cls, engine: Engine, embedder: Embedder, min_similarity: float | None = None
    ) -> "KbRetriever":
        return cls(lambda: Session(engine), embedder, min_similarity)

    def _search(self, query: PolicyQuery) -> PolicySearchResult:
        with self._sessions() as session:
            return search(
                query, session=session, embedder=self._embedder, min_similarity=self._min_similarity
            )

    async def search(self, query: PolicyQuery) -> PolicySearchResult:
        return await asyncio.to_thread(self._search, query)
