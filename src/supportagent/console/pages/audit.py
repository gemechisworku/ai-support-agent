"""Call audit trail (T-CON-08, CON-R10).

Every gateway tool call of one call, in time order (from the bank's audit log): what the agent asked
for, whether the gateway allowed it, how long it took, and for which customer. Denials stand out.
"""

import streamlit as st

from supportagent.console import clients as cl
from supportagent.console import ui, views

bank = cl.get_clients().bank

st.title("Audit trail")
st.caption("Pick a recent call or paste a call id (e.g. the one printed by scripts/demo_m1.py).")

recent = ui.api(bank.interactions, 20, 0)
choices = {"": ""} | {
    f"{views.date(i.started_at)} · {i.direction.value} · {i.contact_result.value} · {i.call_id}": i.call_id
    for i in recent.items
}
picked = st.selectbox("Recent calls", list(choices), key="recent", format_func=lambda k: k or "—")
typed = st.text_input("Call id", key="call_id", value=st.query_params.get("call_id", ""))
call_id = typed.strip() or choices[picked]

if call_id:
    rows = ui.api(bank.audit, call_id).items
    if not rows:
        st.info(f"No audit rows for call {call_id}.")
    else:
        denied = sum(r.decision != "allow" for r in rows)
        cols = st.columns(3)
        cols[0].metric("Tool calls", len(rows))
        cols[1].metric("Denied or failed", denied)
        cols[2].metric("Total latency (ms)", sum(r.latency_ms for r in rows))
        if denied:
            st.error(f"{denied} tool call(s) were denied or failed — marked ⛔ / ⚠️ below.")
        st.dataframe(views.audit_rows(rows), hide_index=True, width="stretch")
