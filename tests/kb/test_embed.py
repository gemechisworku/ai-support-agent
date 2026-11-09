"""T-KB-04, T-PLAT-07 · KB-R4: Embedder protocol, OpenAICompatEmbedder (httpx) and offline HashEmbedder."""

import json
import math
import subprocess
import sys
from collections.abc import Callable

import httpx
import pytest

from supportagent.common.settings import Settings, get_settings
from supportagent.kb.embed import (
    DOCUMENT_PREFIX,
    EMBED_DIM,
    QUERY_PREFIX,
    Embedder,
    EmbedderError,
    HashEmbedder,
    OpenAICompatEmbedder,
    get_embedder,
)


def _norm(v: list[float]) -> float:
    return math.sqrt(sum(x * x for x in v))


def _cos(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))  # inputs are unit-norm


# --------------------------------------------------------------------------- HashEmbedder


def test_hash_embedder_shape_and_unit_norm() -> None:
    emb = HashEmbedder()
    vecs = emb.embed_documents(["Late fees on a personal loan", "x", ""])
    assert emb.dim == EMBED_DIM == 768
    assert len(vecs) == 3
    for v in vecs:
        assert len(v) == 768
        assert all(isinstance(x, float) for x in v)
        assert _norm(v) == pytest.approx(1.0)
    assert len(emb.embed_query("late fee")) == 768


def test_hash_embedder_is_deterministic_across_processes() -> None:
    here = HashEmbedder().embed_documents(["x", "What happens if I pay late?"])
    code = (
        "import json; from supportagent.kb.embed import HashEmbedder; "
        "print(json.dumps(HashEmbedder().embed_documents(['x', 'What happens if I pay late?'])))"
    )
    # A fresh interpreter with a different hash seed: Python's hash() randomisation must not matter.
    out = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=True,
        env={"PYTHONHASHSEED": "123"},
    )
    assert json.loads(out.stdout) == here


def test_hash_embedder_query_and_document_agree() -> None:
    emb = HashEmbedder()
    assert emb.embed_query("grace period") == emb.embed_documents(["grace period"])[0]


def test_hash_embedder_similarity_follows_shared_words() -> None:
    emb = HashEmbedder()
    q = emb.embed_query("late fee for a personal loan")
    near = emb.embed_documents(["Personal loan late fee: 5% of the missed instalment"])[0]
    far = emb.embed_documents(["Branches open at nine on weekdays"])[0]
    assert _cos(q, near) > _cos(q, far)
    assert _cos(q, q) == pytest.approx(1.0)


def test_hash_embedder_is_case_and_punctuation_insensitive() -> None:
    emb = HashEmbedder()
    assert emb.embed_query("Late fee!") == emb.embed_query("late fee")


def test_embedders_satisfy_protocol() -> None:
    assert isinstance(HashEmbedder(), Embedder)
    assert isinstance(OpenAICompatEmbedder(BASE, "nomic-embed-text-v1.5"), Embedder)


# --------------------------------------------------------------------------- OpenAICompatEmbedder

BASE = "http://llama.test:8082/v1"


def _vector(seed: int, dim: int = 768) -> list[float]:
    """A non-normalised vector, so the test also proves the embedder normalises."""
    return [float((seed + i) % 7 + 1) for i in range(dim)]


def _data(vectors: list[list[float]]) -> dict[str, object]:
    """An OpenAI-style `/embeddings` response body."""
    return {
        "object": "list",
        "model": "nomic-embed-text-v1.5",
        "data": [{"object": "embedding", "index": i, "embedding": v} for i, v in enumerate(vectors)],
    }


def _compat(handler: Callable[[httpx.Request], httpx.Response], batch_size: int = 32) -> OpenAICompatEmbedder:
    transport = httpx.MockTransport(handler)
    return OpenAICompatEmbedder(BASE, "nomic-embed-text-v1.5", batch_size=batch_size, transport=transport)


def test_request_shape_prefixes_and_batching() -> None:
    calls: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        calls.append({"path": request.url.path, **body})
        return httpx.Response(200, json=_data([_vector(i) for i, _ in enumerate(body["input"])]))

    emb = _compat(handler, batch_size=32)
    texts = [f"chunk {i}" for i in range(70)]
    vecs = emb.embed_documents(texts)

    assert [len(c["input"]) for c in calls] == [32, 32, 6]
    assert all(c["path"] == "/v1/embeddings" and c["model"] == "nomic-embed-text-v1.5" for c in calls)
    assert calls[0]["input"][0] == f"{DOCUMENT_PREFIX}chunk 0"
    assert calls[2]["input"][-1] == f"{DOCUMENT_PREFIX}chunk 69"
    assert len(vecs) == 70
    assert all(len(v) == 768 and _norm(v) == pytest.approx(1.0) for v in vecs)


def test_query_prefix() -> None:
    seen: list[list[str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content)["input"])
        return httpx.Response(200, json=_data([_vector(1)]))

    vec = _compat(handler).embed_query("Can I pay early?")
    assert seen == [[f"{QUERY_PREFIX}Can I pay early?"]]
    assert QUERY_PREFIX == "search_query: " and DOCUMENT_PREFIX == "search_document: "
    assert _norm(vec) == pytest.approx(1.0)


def test_vectors_are_ordered_by_index() -> None:
    a, b = _vector(1), _vector(4)

    def handler(request: httpx.Request) -> httpx.Response:
        body = _data([a, b])
        body["data"] = list(reversed(body["data"]))  # type: ignore[call-overload]
        return httpx.Response(200, json=body)

    first, second = _compat(handler).embed_documents(["a", "b"])
    assert first == pytest.approx([x / _norm(a) for x in a])
    assert second == pytest.approx([x / _norm(b) for x in b])


def test_empty_input_makes_no_request() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no request expected")

    assert _compat(handler).embed_documents([]) == []


def test_dimension_mismatch_is_a_clear_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_data([_vector(1, dim=384)]))

    with pytest.raises(EmbedderError, match=r"384.*768"):
        _compat(handler).embed_query("hello")


def test_wrong_number_of_vectors() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_data([_vector(1)]))

    with pytest.raises(EmbedderError, match="2 texts"):
        _compat(handler).embed_documents(["a", "b"])


def test_connection_failure_is_a_clear_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    with pytest.raises(EmbedderError, match=r"not reachable.*llama.test.*llama-server --embedding"):
        _compat(handler).embed_query("hello")


def test_timeout_is_a_clear_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    with pytest.raises(EmbedderError, match="timed out"):
        _compat(handler).embed_query("hello")


def test_server_without_embedding_flag_hints_at_it() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(501, json={"error": {"message": "This server does not support embeddings."}})

    with pytest.raises(EmbedderError, match=r"HTTP 501.*--embedding"):
        _compat(handler).embed_query("hello")


def test_server_error_and_bad_payload() -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="internal error")

    with pytest.raises(EmbedderError, match="HTTP 500"):
        _compat(boom).embed_query("hello")

    def junk(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"unexpected": True})

    with pytest.raises(EmbedderError, match="embedding"):
        _compat(junk).embed_query("hello")


def test_rejects_zero_vector() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_data([[0.0] * 768]))

    with pytest.raises(EmbedderError, match="zero"):
        _compat(handler).embed_query("hello")


def test_batch_size_must_be_positive() -> None:
    with pytest.raises(ValueError, match="batch_size"):
        OpenAICompatEmbedder(BASE, "m", batch_size=0)


# --------------------------------------------------------------------------- get_embedder


def test_get_embedder_selects_fake(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KB_EMBEDDER", "fake")
    get_settings.cache_clear()
    emb = get_embedder(get_settings())
    assert isinstance(emb, HashEmbedder)
    assert emb.name == "hash-v1"


def test_get_embedder_defaults_to_llamacpp(settings: Settings) -> None:
    emb = get_embedder(settings)
    assert isinstance(emb, OpenAICompatEmbedder)
    assert emb.base_url == "http://localhost:8082/v1"
    assert emb.name == "llamacpp/nomic-embed-text-v1.5"


def test_get_embedder_selects_llamacpp_from_settings(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("KB_EMBEDDER", "llamacpp")
    monkeypatch.setenv("EMBED_URL", "http://embed.example:9000/v1/")
    monkeypatch.setenv("EMBED_MODEL", "nomic-embed-text-v1.5")
    get_settings.cache_clear()
    emb = get_embedder()  # defaults to get_settings()
    assert isinstance(emb, OpenAICompatEmbedder)
    assert emb.base_url == "http://embed.example:9000/v1"
    assert emb.model == "nomic-embed-text-v1.5"
    assert emb.name == "llamacpp/nomic-embed-text-v1.5"
