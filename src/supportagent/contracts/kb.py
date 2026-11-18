"""Policy retrieval contracts (KB-R6, KB-R7): what the gateway's `search_policy` tool sends and gets back.

Produced by `supportagent.kb.retrieval.search` (T-KB-06); consumed by the gateway (T-GW-08), which
adds each chunk's `citation` to the call's `kb_sources`.
"""

import datetime as dt
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ProductCode = Literal["PERSONAL", "AUTO", "MORTGAGE", "SME_WC", "SALARY_ADV"]
PRODUCT_CODES: tuple[ProductCode, ...] = ("PERSONAL", "AUTO", "MORTGAGE", "SME_WC", "SALARY_ADV")
Audience = Literal["customer", "internal"]


class _Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class PolicyQuery(_Contract):
    """A policy question. `product=None` searches every product; `as_of=None` means today."""

    query: str = Field(min_length=1, max_length=500)
    product: ProductCode | None = None
    audience: Audience = "customer"
    as_of: dt.date | None = None
    top_k: int = Field(4, ge=1, le=8)


class PolicyChunk(_Contract):
    doc_id: str
    version: str
    title: str
    section: str  # heading trail, e.g. "Late fees > Auto loan"
    product: ProductCode | Literal["all"]
    text: str
    rrf_score: float = Field(ge=0)
    similarity: float | None = None  # cosine similarity to the query embedding

    @property
    def citation(self) -> str:
        """The `kb_sources` entry for this chunk: `doc_id@version#section`."""
        return f"{self.doc_id}@{self.version}#{self.section}"


class PolicySearchResult(_Contract):
    chunks: list[PolicyChunk]
    confident: bool  # False → the agent offers a follow-up instead of answering (KB-R7)
    best_similarity: float


# --------------------------------------------------------------------------- browse (T-GW-12, console)


class KbDocumentVersion(_Contract):
    version: str
    effective_date: dt.date
    chunk_count: int = Field(ge=0)


class KbDocumentSummary(_Contract):
    """One policy document with every ingested version (newest effective date first)."""

    doc_id: str
    title: str  # title of the newest version
    product: ProductCode | Literal["all"]
    audience: Audience
    versions: list[KbDocumentVersion]


class KbChunkView(_Contract):
    ord: int = Field(ge=0)
    section: str
    text: str
    token_count: int = Field(ge=0)


class KbDocumentDetail(_Contract):
    """One document version and its chunks in reading order."""

    doc_id: str
    version: str
    title: str
    product: ProductCode | Literal["all"]
    audience: Audience
    effective_date: dt.date
    chunks: list[KbChunkView]
