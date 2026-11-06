"""T-KB-04 · KB-R4 (eval): the real local Ollama returns usable 768-d nomic embeddings.

Run with `uv run pytest -m eval tests/kb/test_embed_ollama.py` after `ollama pull nomic-embed-text`.
"""

import math

import httpx
import pytest

from supportagent.common.settings import Settings
from supportagent.kb.embed import EMBED_DIM, OllamaEmbedder

pytestmark = pytest.mark.eval


@pytest.fixture
def ollama(settings: Settings) -> OllamaEmbedder:
    url = settings.models.ollama_url
    try:
        httpx.get(f"{url}/api/tags", timeout=2).raise_for_status()
    except httpx.HTTPError:
        pytest.skip(f"Ollama not reachable at {url} (brew services start ollama)")
    return OllamaEmbedder(url, settings.models.embed_model)


def test_real_ollama_returns_unit_768d_vectors(ollama: OllamaEmbedder) -> None:
    docs = ollama.embed_documents(["Late fees are 5% of the missed instalment.", "Branches open at nine."])
    query = ollama.embed_query("How much is the late fee?")
    for v in [*docs, query]:
        assert len(v) == EMBED_DIM
        assert math.sqrt(sum(x * x for x in v)) == pytest.approx(1.0, abs=1e-6)
    sim = [sum(a * b for a, b in zip(query, d, strict=True)) for d in docs]
    assert sim[0] > sim[1]  # the late-fee chunk is closer to the late-fee question
