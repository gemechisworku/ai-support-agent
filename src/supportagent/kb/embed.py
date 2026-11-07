"""Embeddings for the policy knowledge base (KB-R4).

Everything that needs vectors goes through the `Embedder` protocol:

- `OpenAICompatEmbedder` — the default: `nomic-embed-text-v1.5` (768-d) served by the native llama.cpp
  `llama-server --embedding` on the Mac, called with the OpenAI-style `POST {EMBED_URL}/embeddings` in
  batches. nomic expects task prefixes, so documents are sent as `search_document: …` and queries as
  `search_query: …`.
- `HashEmbedder` — offline and deterministic, for unit/integration tests and CI. It hashes lower-cased
  word unigrams and bigrams into 768 buckets, so texts that share words get similar vectors.

Both return unit-norm (L2) 768-d `list[float]`, so cosine similarity is a plain dot product.
`get_embedder(settings)` picks one from `KB_EMBEDDER=llamacpp|fake`.
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
    name: str  # recorded with ingested chunks, e.g. "llamacpp/nomic-embed-text-v1.5" or "hash-v1"

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


class OpenAICompatEmbedder:
    """nomic embeddings through an OpenAI-compatible `/embeddings` endpoint (llama.cpp `llama-server`).

    `base_url` includes the `/v1` part, e.g. `http://localhost:8082/v1`. Inputs are batched and get
    nomic's task prefixes. `provider` only names the backend in `name` (stored with ingested chunks).
    """

    def __init__(
        self,
        base_url: str,
        model: str,
        *,
        provider: str = "llamacpp",
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
        self.name = f"{provider}/{model}"
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
            resp = self._client.post("/embeddings", json={"model": self.model, "input": inputs})
        except httpx.TimeoutException as exc:
            raise EmbedderError(
                f"embeddings server at {self.base_url} timed out embedding {len(inputs)} texts"
            ) from exc
        except httpx.TransportError as exc:
            raise EmbedderError(
                f"embeddings server not reachable at {self.base_url} ({exc}); start it with "
                "`make models-up` or the `llama-server --embedding` command in docs/dev-setup.md"
            ) from exc
        if resp.status_code != 200:
            raise EmbedderError(
                f"embeddings server {self.base_url}/embeddings returned HTTP {resp.status_code}: "
                f"{resp.text[:200]} (was llama-server started with --embedding?)"
            )
        return self._parse(resp, len(inputs))

    def _parse(self, resp: httpx.Response, expected: int) -> list[list[float]]:
        try:
            payload: Any = resp.json()
            items = sorted(payload["data"], key=lambda item: item.get("index", 0))
            vectors = [item["embedding"] for item in items]
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            raise EmbedderError("embeddings response has no 'data[].embedding' list") from exc
        if len(vectors) != expected:
            raise EmbedderError(f"embeddings server returned {len(vectors)} vectors for {expected} texts")
        for vec in vectors:
            if not isinstance(vec, list) or len(vec) != self.dim:
                got = len(vec) if isinstance(vec, list) else "non-list"
                raise EmbedderError(
                    f"{self.model} returned {got}-d vectors but the KB schema expects {self.dim}-d"
                    " (was llama-server started with --pooling mean?)"
                )
        return [_normalise(v) for v in vectors]


def get_embedder(settings: Settings | None = None) -> Embedder:
    """The embedder selected by `KB_EMBEDDER` (`llamacpp` default, `fake` for tests and CI)."""
    s = settings or get_settings()
    if s.data.kb_embedder == "fake":
        return HashEmbedder()
    return OpenAICompatEmbedder(s.models.embed_url, s.models.embed_model)
