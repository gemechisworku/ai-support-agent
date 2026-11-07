"""Policy knowledge-base explorer and search playground (T-CON-07, CON-R9).

Lists the documents in the knowledge base, shows one document version's chunks, and runs the same
retrieval the agent's `search_policy` tool uses (through the gateway's service API), so you see
exactly which cited text the agent would be allowed to say.
"""

import streamlit as st

from supportagent.console import clients as cl
from supportagent.console import ui, views
from supportagent.contracts.kb import PolicyQuery

PRODUCTS = ["", "PERSONAL", "AUTO", "MORTGAGE", "SME_WC", "SALARY_ADV"]
LOW_CONFIDENCE_NOTE = (
    "Low confidence: the agent would not answer from these chunks; it would say it is not sure and "
    "offer a follow-up."
)

gateway = cl.get_clients().gateway

st.title("Policies")
documents_tab, search_tab = st.tabs(["Documents", "Search playground"])

with documents_tab:
    docs = ui.api(gateway.kb_documents)
    st.caption(f"{len(docs)} documents in the knowledge base.")
    st.dataframe(
        [
            {
                "Document": d.doc_id,
                "Title": d.title,
                "Product": d.product,
                "Audience": d.audience,
                "Versions": ", ".join(f"{v.version} (from {v.effective_date})" for v in d.versions),
                "Chunks (latest)": d.versions[0].chunk_count if d.versions else 0,
            }
            for d in docs
        ],
        hide_index=True,
        width="stretch",
    )
    by_id = {d.doc_id: d for d in docs}
    if by_id:
        doc_id = st.selectbox("Open a document", list(by_id), key="doc", format_func=lambda i: by_id[i].title)
        versions = [""] + [v.version for v in by_id[doc_id].versions]
        version = st.selectbox(
            "Version", versions, key="version", format_func=lambda v: v or "effective today"
        )
        detail = ui.try_api(gateway.kb_document, doc_id, version or None)
        if detail is not None:
            st.subheader(f"{detail.title} · v{detail.version}")
            st.caption(
                f"{detail.doc_id} · product {detail.product} · audience {detail.audience} · "
                f"effective {detail.effective_date} · {len(detail.chunks)} chunks"
            )
            for chunk in detail.chunks:
                with st.expander(f"#{chunk.ord} · {chunk.section} · {chunk.token_count} tokens"):
                    st.markdown(chunk.text)

with search_tab:
    st.caption("Runs the agent's own retrieval (hybrid vector + full text, top 4, confidence threshold).")
    query = st.text_input(
        "Question", key="query", placeholder="e.g. What is the late fee on a personal loan?"
    )
    product = st.selectbox("Product", PRODUCTS, key="product", format_func=lambda p: p or "any")
    if query.strip():
        result = ui.api(gateway.kb_search, PolicyQuery(query=query.strip(), product=product or None))
        cols = st.columns(2)
        cols[0].metric("Confident", "yes" if result.confident else "no")
        cols[1].metric("Best similarity", f"{result.best_similarity:.3f}")
        if not result.confident:
            st.warning(LOW_CONFIDENCE_NOTE)
        if result.chunks:
            st.dataframe(views.chunk_rows(result.chunks), hide_index=True, width="stretch")
            for hit in result.chunks:
                with st.expander(hit.citation):
                    st.markdown(hit.text)
        else:
            st.info("No chunks matched.")
