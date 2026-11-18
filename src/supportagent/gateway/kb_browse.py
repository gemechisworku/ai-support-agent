"""Read-only knowledge-base browse API for the console (GW-R12, T-GW-12).

Service scope (`Authorization: Bearer $GATEWAY_SERVICE_TOKEN`), audited with `call_id="service"`:

- `GET  /service/kb/documents` → `list[KbDocumentSummary]` (every document, every ingested version)
- `GET  /service/kb/documents/{doc_id}?version=` → `KbDocumentDetail` (default: the version effective
  today, else the newest one) — 404 for an unknown document or version
- `POST /service/kb/search` (`PolicyQuery`) → `PolicySearchResult`, through the same `Retriever`
  (embedder and confidence threshold) as the agent's `search_policy`, so the console shows exactly
  what the agent would get

It reads only the `kb` database; nothing here calls the bank (apart from writing audit rows).
"""

import asyncio
from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import Annotated, Any, Protocol

from fastapi import APIRouter, HTTPException, Query, Request, status
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from supportagent.common import clock
from supportagent.contracts.kb import (
    KbChunkView,
    KbDocumentDetail,
    KbDocumentSummary,
    KbDocumentVersion,
    PolicyQuery,
    PolicySearchResult,
)
from supportagent.gateway.auth import gateway_of, service_call
from supportagent.kb.models import Chunk, Document


class KbBrowser(Protocol):
    def list_documents(self) -> list[KbDocumentSummary]: ...

    def document(self, doc_id: str, version: str | None = None) -> KbDocumentDetail | None: ...


class SqlKbBrowser:
    """`KbBrowser` over the `kb` database (synchronous; the router calls it in a worker thread)."""

    def __init__(self, sessions: Callable[[], AbstractContextManager[Session]]) -> None:
        self._sessions = sessions

    @classmethod
    def from_engine(cls, engine: Engine) -> "SqlKbBrowser":
        return cls(lambda: Session(engine))

    def list_documents(self) -> list[KbDocumentSummary]:
        with self._sessions() as session:
            rows = session.execute(
                select(Document, func.count(Chunk.id))
                .outerjoin(Chunk, Chunk.document_id == Document.id)
                .group_by(Document.id)
                .order_by(Document.doc_id, Document.effective_date.desc(), Document.version.desc())
            ).all()
        by_doc: dict[str, tuple[Document, list[KbDocumentVersion]]] = {}
        for doc, n_chunks in rows:
            _, versions = by_doc.setdefault(doc.doc_id, (doc, []))
            versions.append(
                KbDocumentVersion(
                    version=doc.version, effective_date=doc.effective_date, chunk_count=n_chunks
                )
            )
        return [
            KbDocumentSummary(
                doc_id=doc_id,
                title=newest.title,
                product=newest.product,  # validated by the contract
                audience=newest.audience,  # validated by the contract
                versions=versions,
            )
            for doc_id, (newest, versions) in by_doc.items()
        ]

    def document(self, doc_id: str, version: str | None = None) -> KbDocumentDetail | None:
        with self._sessions() as session:
            stmt = select(Document).where(Document.doc_id == doc_id)
            if version is not None:
                doc = session.scalars(stmt.where(Document.version == version)).first()
            else:
                ordered = stmt.order_by(Document.effective_date.desc(), Document.version.desc())
                doc = session.scalars(ordered.where(Document.effective_date <= clock.today())).first()
                doc = doc or session.scalars(ordered).first()  # nothing effective yet: the newest
            if doc is None:
                return None
            chunks = session.scalars(
                select(Chunk).where(Chunk.document_id == doc.id).order_by(Chunk.ord)
            ).all()
            return KbDocumentDetail(
                doc_id=doc.doc_id,
                version=doc.version,
                title=doc.title,
                product=doc.product,  # validated by the contract
                audience=doc.audience,  # validated by the contract
                effective_date=doc.effective_date,
                chunks=[
                    KbChunkView(ord=c.ord, section=c.section_path, text=c.text, token_count=c.token_count)
                    for c in chunks
                ],
            )


def _unavailable(what: str) -> HTTPException:
    return HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail=f"{what} is not configured")


router = APIRouter(prefix="/service/kb", tags=["service: knowledge base"])


@router.get("/documents", response_model=list[KbDocumentSummary])
async def list_documents(request: Request) -> list[KbDocumentSummary]:
    gw = gateway_of(request)

    async def produce() -> list[KbDocumentSummary]:
        if gw.kb_browser is None:
            raise _unavailable("knowledge-base browsing")
        return await asyncio.to_thread(gw.kb_browser.list_documents)

    return await service_call(request, "kb.list_documents", {}, produce)


@router.get("/documents/{doc_id}", response_model=KbDocumentDetail)
async def get_document(
    request: Request, doc_id: str, version: Annotated[str | None, Query(max_length=40)] = None
) -> KbDocumentDetail:
    gw = gateway_of(request)
    args: dict[str, Any] = {"doc_id": doc_id, "version": version}

    async def produce() -> KbDocumentDetail:
        if gw.kb_browser is None:
            raise _unavailable("knowledge-base browsing")
        detail = await asyncio.to_thread(gw.kb_browser.document, doc_id, version)
        if detail is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="document or version not found")
        return detail

    return await service_call(request, "kb.get_document", args, produce)


@router.post("/search", response_model=PolicySearchResult)
async def search(request: Request, query: PolicyQuery) -> PolicySearchResult:
    gw = gateway_of(request)

    async def produce() -> PolicySearchResult:
        if gw.retriever is None:
            raise _unavailable("policy search")
        return await gw.retriever.search(query)

    return await service_call(request, "kb.search", query.model_dump(mode="json"), produce)
