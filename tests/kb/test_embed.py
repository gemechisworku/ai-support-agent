"""T-KB-04 · KB-R4: Embedder protocol, OllamaEmbedder (over httpx) and the offline HashEmbedder."""

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
    OllamaEmbedder,
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
    assert isinstance(OllamaEmbedder("http://ollama.test", "nomic-embed-text"), Embedder)


# --------------------------------------------------------------------------- OllamaEmbedder


def _vector(seed: int, dim: int = 768) -> list[float]:
    """A non-normalised vector, so the test also proves the embedder normalises."""
    return [float((seed + i) % 7 + 1) for i in range(dim)]


def _ollama(handler: Callable[[httpx.Request], httpx.Response], **kw: object) -> OllamaEmbedder:
    return OllamaEmbedder(
        "http://ollama.test", "nomic-embed-text", transport=httpx.MockTransport(handler), **kw
    )  # type: ignore[arg-type]


def test_ollama_request_shape_prefixes_and_batching() -> None:
    calls: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        calls.append({"path": request.url.path, **body})
        return httpx.Response(200, json={"embeddings": [_vector(i) for i, _ in enumerate(body["input"])]})

    emb = _ollama(handler, batch_size=32)
    texts = [f"chunk {i}" for i in range(70)]
    vecs = emb.embed_documents(texts)

    assert [len(c["input"]) for c in calls] == [32, 32, 6]
    assert all(c["path"] == "/api/embed" and c["model"] == "nomic-embed-text" for c in calls)
    assert calls[0]["input"][0] == f"{DOCUMENT_PREFIX}chunk 0"
    assert calls[2]["input"][-1] == f"{DOCUMENT_PREFIX}chunk 69"
    assert len(vecs) == 70
    assert all(len(v) == 768 and _norm(v) == pytest.approx(1.0) for v in vecs)


def test_ollama_query_prefix() -> None:
    seen: list[list[str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content)["input"])
        return httpx.Response(200, json={"embeddings": [_vector(1)]})

    vec = _ollama(handler).embed_query("Can I pay early?")
    assert seen == [[f"{QUERY_PREFIX}Can I pay early?"]]
    assert QUERY_PREFIX == "search_query: " and DOCUMENT_PREFIX == "search_document: "
    assert _norm(vec) == pytest.approx(1.0)


def test_ollama_empty_input_makes_no_request() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no request expected")

    assert _ollama(handler).embed_documents([]) == []


def test_ollama_dimension_mismatch_is_a_clear_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"embeddings": [_vector(1, dim=384)]})

    with pytest.raises(EmbedderError, match=r"384.*768"):
        _ollama(handler).embed_query("hello")


def test_ollama_wrong_number_of_vectors() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"embeddings": [_vector(1)]})

    with pytest.raises(EmbedderError, match="2 texts"):
        _ollama(handler).embed_documents(["a", "b"])


def test_ollama_connection_failure_is_a_clear_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    with pytest.raises(EmbedderError, match=r"not reachable.*ollama.test"):
        _ollama(handler).embed_query("hello")


def test_ollama_timeout_is_a_clear_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    with pytest.raises(EmbedderError, match="timed out"):
        _ollama(handler).embed_query("hello")


def test_ollama_missing_model_hints_at_pull() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": 'model "nomic-embed-text" not found, try pulling it first'})

    with pytest.raises(EmbedderError, match="ollama pull nomic-embed-text"):
        _ollama(handler).embed_query("hello")


def test_ollama_server_error_and_bad_payload() -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="internal error")

    with pytest.raises(EmbedderError, match="HTTP 500"):
        _ollama(boom).embed_query("hello")

    def junk(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"unexpected": True})

    with pytest.raises(EmbedderError, match="embeddings"):
        _ollama(junk).embed_query("hello")


def test_ollama_rejects_zero_vector() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"embeddings": [[0.0] * 768]})

    with pytest.raises(EmbedderError, match="zero"):
        _ollama(handler).embed_query("hello")


def test_ollama_batch_size_must_be_positive() -> None:
    with pytest.raises(ValueError, match="batch_size"):
        OllamaEmbedder("http://ollama.test", "m", batch_size=0)


# --------------------------------------------------------------------------- get_embedder


def test_get_embedder_selects_fake(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KB_EMBEDDER", "fake")
    get_settings.cache_clear()
    emb = get_embedder(get_settings())
    assert isinstance(emb, HashEmbedder)
    assert emb.name == "hash-v1"


def test_get_embedder_selects_ollama_from_settings(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("KB_EMBEDDER", "ollama")
    monkeypatch.setenv("OLLAMA_URL", "http://ollama.example:11434")
    monkeypatch.setenv("EMBED_MODEL", "nomic-embed-text")
    get_settings.cache_clear()
    emb = get_embedder()  # defaults to get_settings()
    assert isinstance(emb, OllamaEmbedder)
    assert emb.base_url == "http://ollama.example:11434"
    assert emb.model == "nomic-embed-text"
    assert emb.name == "ollama/nomic-embed-text"
