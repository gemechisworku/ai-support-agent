"""Embeddings for the policy knowledge base (KB-R4).

Everything that needs vectors goes through the `Embedder` protocol:

- `OllamaEmbedder` — the default: `nomic-embed-text` (768-d) served by the native Ollama on the Mac,
  called with `POST {OLLAMA_URL}/api/embed` in batches. nomic expects task prefixes, so documents are
  sent as `search_document: …` and queries as `search_query: …`.
- `HashEmbedder` — offline and deterministic, for unit/integration tests and CI. It hashes lower-cased
  word unigrams and bigrams into 768 buckets, so texts that share words get similar vectors.

Both return unit-norm (L2) 768-d `list[float]`, so cosine similarity is a plain dot product.
`get_embedder(settings)` picks one from `KB_EMBEDDER=ollama|fake`.
"""

from __future__ import annotations

import hashlib
import itertools
import math
import re
from collections.abc import Sequence
from typing import Any, Protocol, runtime_checkable

import httpx

from supportagent.common.settings import Settings, get_settings

EMBED_DIM = 768
DOCUMENT_PREFIX = "search_document: "
QUERY_PREFIX = "search_query: "

_WORD = re.compile(r"\w+")


class EmbedderError(RuntimeError):
    """The embedding backend failed or returned something unusable. The message says how to fix it."""


@runtime_checkable
class Embedder(Protocol):
    """What ingestion and retrieval need from an embedding model."""

    dim: int
    name: str  # recorded with ingested chunks, e.g. "ollama/nomic-embed-text" or "hash-v1"

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


def _normalise(vec: Sequence[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in vec))
    if norm == 0.0:
        raise EmbedderError("embedding is a zero vector and cannot be normalised")
    return [float(x) / norm for x in vec]


class HashEmbedder:
    """Deterministic lexical embedder: no network, no model, identical output in every process."""

    name = "hash-v1"

    def __init__(self, dim: int = EMBED_DIM) -> None:
        self.dim = dim

    def _features(self, text: str) -> list[str]:
        words = _WORD.findall(text.lower())
        if not words:
            return ["<empty>"]
        return words + [f"{a} {b}" for a, b in itertools.pairwise(words)]

    def _embed(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        for feature in self._features(text):
            digest = hashlib.sha256(feature.encode("utf-8")).digest()
            vec[int.from_bytes(digest[:8], "big") % self.dim] += 1.0
        return _normalise(vec)

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._embed(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._embed(text)


class OllamaEmbedder:
    """`nomic-embed-text` through Ollama's `/api/embed`, batched, with nomic's task prefixes."""

    def __init__(
        self,
        base_url: str,
        model: str,
        *,
        dim: int = EMBED_DIM,
        batch_size: int = 32,
        timeout_s: float = 30.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if batch_size < 1:
            raise ValueError("batch_size must be >= 1")
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.dim = dim
        self.batch_size = batch_size
        self.name = f"ollama/{model}"
        self._client = httpx.Client(base_url=self.base_url, timeout=timeout_s, transport=transport)

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for start in range(0, len(texts), self.batch_size):
            batch = texts[start : start + self.batch_size]
            out.extend(self._embed([DOCUMENT_PREFIX + t for t in batch]))
        return out

    def embed_query(self, text: str) -> list[float]:
        return self._embed([QUERY_PREFIX + text])[0]

    def close(self) -> None:
        self._client.close()

    def _embed(self, inputs: list[str]) -> list[list[float]]:
        try:
            resp = self._client.post("/api/embed", json={"model": self.model, "input": inputs})
        except httpx.TimeoutException as exc:
            raise EmbedderError(f"Ollama at {self.base_url} timed out embedding {len(inputs)} texts") from exc
        except httpx.TransportError as exc:
            raise EmbedderError(
                f"Ollama not reachable at {self.base_url} ({exc}); start it with `brew services start ollama`"
            ) from exc
        if resp.status_code == 404 and "not found" in resp.text:
            raise EmbedderError(f"Ollama has no model {self.model!r}; run `ollama pull {self.model}`")
        if resp.status_code != 200:
            raise EmbedderError(f"Ollama /api/embed returned HTTP {resp.status_code}: {resp.text[:200]}")
        return self._parse(resp, len(inputs))

    def _parse(self, resp: httpx.Response, expected: int) -> list[list[float]]:
        try:
            payload: Any = resp.json()
            vectors = payload["embeddings"]
        except (ValueError, KeyError, TypeError) as exc:
            raise EmbedderError("Ollama response has no 'embeddings' list") from exc
        if not isinstance(vectors, list) or len(vectors) != expected:
            got = len(vectors) if isinstance(vectors, list) else "no"
            raise EmbedderError(f"Ollama returned {got} vectors for {expected} texts")
        for vec in vectors:
            if len(vec) != self.dim:
                raise EmbedderError(
                    f"{self.model} returned {len(vec)}-d vectors but the KB schema expects {self.dim}-d"
                )
        return [_normalise(v) for v in vectors]


def get_embedder(settings: Settings | None = None) -> Embedder:
    """The embedder selected by `KB_EMBEDDER` (`ollama` default, `fake` for tests and CI)."""
    s = settings or get_settings()
    if s.data.kb_embedder == "fake":
        return HashEmbedder()
    return OllamaEmbedder(s.models.ollama_url, s.models.embed_model)
