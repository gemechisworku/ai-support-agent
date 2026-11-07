"""Persona test sheet (T-CON-08, CON-R10).

The 12 scripted personas with their situation, expected outcome and softphone extension, plus the
synthetic verification details a tester gives when role-playing them. This is the only page that
shows a date of birth: these are fake test identities from the mock bank's seed.
"""

import streamlit as st

from supportagent.console import clients as cl
from supportagent.console import ui, views

bank = cl.get_clients().bank

st.title("Test personas")
st.info(
    "Synthetic **test identities** for role-playing calls (name, DOB and loan last 4 are what the agent "
    "asks for during verification). None of them is a real person.",
    icon="🎭",
)
cards = ui.api(bank.personas)
st.caption(f"{len(cards)} personas")
st.dataframe(views.persona_rows(cards), hide_index=True, width="stretch")

options = {f"{p.customer_id} · {p.full_name}": p.customer_id for p in cards}
if options:
    choice = st.selectbox("Open a persona in the customer explorer", list(options), key="persona")
    if st.button("Open customer", key="open_customer"):
        st.switch_page("pages/customers.py", query_params={"customer": str(options[choice])})
