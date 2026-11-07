"""`search_policy` (GW-R7, GW-R10): hybrid KB retrieval, available without verification (U4).

Chunks come back as data: text is sanitised and truncated, never interpreted, and nothing in a chunk
can change the session. Every returned source is recorded on the session and later merged into the
call outcome's `kb_sources`. `low_confidence` mirrors the KB's confidence rule, so the agent offers a
follow-up instead of guessing (F6).
"""

from supportagent.contracts.gateway import PolicySource, SearchPolicyInput, SearchPolicyOutput
from supportagent.contracts.kb import PolicyQuery, ProductCode
from supportagent.gateway.masking import sanitize_text
from supportagent.gateway.tools import ToolContext, ToolDenied, gateway_tool

MAX_CHUNKS = 4


async def _default_product(ctx: ToolContext) -> ProductCode | None:
    """When the caller is verified and has exactly one loan, search that product's policies."""
    if ctx.session.bound_customer_id is None:
        return None
    loans = await ctx.gateway.bank.customer_loans(ctx.session.bound_customer_id)
    return loans[0].product_code if len(loans) == 1 else None


@gateway_tool("search_policy", scope="any", input_model=SearchPolicyInput, output_model=SearchPolicyOutput)
async def search_policy(ctx: ToolContext, inp: SearchPolicyInput) -> SearchPolicyOutput:
    retriever = ctx.gateway.retriever
    if retriever is None:
        raise ToolDenied("upstream_error", "policy search is not configured")
    product = inp.product or await _default_product(ctx)
    try:
        result = await retriever.search(PolicyQuery(query=inp.query, product=product, top_k=MAX_CHUNKS))
    except Exception as exc:  # embedder or database failure: no answer rather than a wrong one
        raise ToolDenied("upstream_error", "policy search failed") from exc
    sources = [
        PolicySource(
            doc_id=ch.doc_id,
            version=ch.version,
            section=ch.section,
            text=sanitize_text(ch.text),
            score=round(ch.similarity if ch.similarity is not None else ch.rrf_score, 4),
        )
        for ch in result.chunks[:MAX_CHUNKS]
    ]
    for source in sources:
        ctx.session.record_source(source.citation)
    return SearchPolicyOutput(chunks=sources, low_confidence=not result.confident or not sources)
