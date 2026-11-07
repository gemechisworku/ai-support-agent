"""Overview: what the console shows and where the data comes from."""

import streamlit as st

st.title("Support agent console")
st.markdown(
    """
Browse the context the voice agent works with: read-only, straight from the services the agent uses.

- **Customers** (bank API): search customers, then a profile with contact preferences, loans with
  their computed status, the instalment schedule, transactions and call history.
- **Policies** (gateway service API): knowledge-base documents, versions and chunks, plus a search
  playground that runs exactly the agent's `search_policy` retrieval.
- **Test personas** (bank API): the 12 scripted personas and the synthetic details to role-play them.
- **Audit trail** (bank audit log): every gateway tool call of one call, in order.

Numbers are masked to the last 4 digits everywhere except the persona sheet's test identities.
"""
)
