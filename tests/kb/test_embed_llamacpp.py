"""T-PLAT-07 · KB-R4 (eval): the real llama.cpp embeddings server returns usable 768-d nomic embeddings.

Run with `uv run pytest -m eval tests/kb/test_embed_llamacpp.py` while the embeddings server runs
(`make models-up`, or the `llama-server --embedding` command in docs/dev-setup.md).
"""

import math

import httpx
import pytest

from supportagent.common.settings import Settings
from supportagent.kb.embed import EMBED_DIM, OpenAICompatEmbedder

pytestmark = pytest.mark.eval


@pytest.fixture
def embedder(settings: Settings) -> OpenAICompatEmbedder:
    url = settings.models.embed_url
    try:
        httpx.get(f"{url.rstrip('/')}/models", timeout=2).raise_for_status()
    except httpx.HTTPError:
        pytest.skip(f"embeddings server not reachable at {url} (see docs/dev-setup.md)")
    return OpenAICompatEmbedder(url, settings.models.embed_model)


def test_real_server_returns_unit_768d_vectors(embedder: OpenAICompatEmbedder) -> None:
    docs = embedder.embed_documents(["Late fees are 5% of the missed instalment.", "Branches open at nine."])
    query = embedder.embed_query("How much is the late fee?")
    for v in [*docs, query]:
        assert len(v) == EMBED_DIM
        assert math.sqrt(sum(x * x for x in v)) == pytest.approx(1.0, abs=1e-6)
    sim = [sum(a * b for a, b in zip(query, d, strict=True)) for d in docs]
    assert sim[0] > sim[1]  # the late-fee chunk is closer to the late-fee question
