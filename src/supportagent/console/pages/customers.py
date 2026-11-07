"""Customer and loan explorer (T-CON-06, CON-R8).

Search customers, then open one: profile and contact preferences, loans with their computed status,
instalment schedule, transactions and call history. Every figure comes from the bank API; the page
only formats it (`console.views`). Numbers are masked to the last 4 digits.
"""

import streamlit as st

from supportagent.console import clients as cl
from supportagent.console import ui, views

PAGE_SIZE = 25
SEGMENTS = ["", "retail", "premium", "sme"]
BUCKETS = ["", "current", "1-30", "31-60", "61-90", "90+"]

bank = cl.get_clients().bank


def _open(customer_id: int | None) -> None:
    if customer_id is None:
        st.query_params.pop("customer", None)
    else:
        st.query_params["customer"] = str(customer_id)


def search_view() -> None:
    st.title("Customers")
    st.caption("Search by name, phone, customer id or loan last 4. Data from the mock bank API.")
    cols = st.columns([3, 1, 1, 1])
    q = cols[0].text_input("Search", key="q", placeholder="e.g. Frank, 4417, 9002")
    segment = cols[1].selectbox("Segment", SEGMENTS, key="segment", format_func=lambda s: s or "any")
    bucket = cols[2].selectbox("Bucket", BUCKETS, key="bucket", format_func=lambda s: s or "any")
    page_no = int(cols[3].number_input("Page", min_value=1, value=1, step=1, key="page"))
    result = ui.api(
        bank.search_customers,
        q.strip(),
        segment or None,
        bucket or None,
        PAGE_SIZE,
        (page_no - 1) * PAGE_SIZE,
    )
    pages = max(1, -(-result.total // PAGE_SIZE))
    st.write(f"{result.total} customers · page {page_no} of {pages}")
    if not result.items:
        st.info("No customers match.")
        return
    st.dataframe(views.customer_rows(result), hide_index=True, width="stretch")
    options = {f"{s.customer_id} · {s.full_name}": s.customer_id for s in result.items}
    choice = st.selectbox("Open a customer", list(options), key="open_choice")
    if st.button("Open", key="open"):
        _open(options[choice])
        st.rerun()


def detail_view(customer_id: int) -> None:
    customer = ui.api(bank.customer, customer_id)
    loans = ui.api(bank.customer_loans, customer_id)
    statuses = [ui.api(bank.loan_status, loan.id) for loan in loans]
    prefs = ui.api(bank.contact_preferences, customer_id)
    history = ui.api(bank.customer_history, customer_id)

    if st.button("← Back to search", key="back"):
        _open(None)
        st.rerun()
    st.title(customer.full_name)
    st.caption(
        f"Customer {customer.id} · phone {views.mask_number(customer.phone)} · {customer.segment} · "
        f"{customer.timezone} · KYC {customer.kyc_status}"
    )
    for status in statuses:
        st.markdown(f"- {views.loan_headline(status)}")

    profile, loans_tab, schedule, transactions, calls = st.tabs(
        ["Profile", "Loans", "Schedule", "Transactions", "History"]
    )
    with profile:
        st.subheader("Contact preferences")
        st.dataframe(views.preference_rows(prefs), hide_index=True, width="stretch")
        st.subheader("Contact details")
        st.write(f"Email: {customer.email}")
        st.write(f"Address: {customer.address}")
        st.write(f"Preferred language: {customer.preferred_language}")
    with loans_tab:
        if statuses:
            st.dataframe(views.loan_rows(statuses), hide_index=True, width="stretch")
        else:
            st.info("No loans.")
    with schedule:
        if loans:
            labels = {views.mask_number(loan.number): loan for loan in loans}
            loan = labels[st.selectbox("Loan", list(labels), key="schedule_loan")]
            st.dataframe(
                views.installment_rows(ui.api(bank.installments, loan.id)),
                hide_index=True,
                width="stretch",
            )
        else:
            st.info("No loans.")
    with transactions:
        if loans:
            labels = {views.mask_number(loan.number): loan for loan in loans}
            loan = labels[st.selectbox("Loan", list(labels), key="tx_loan")]
            rows = ui.api(bank.transactions, loan.repayment_account_id, loan.id, 20)
            st.caption("Newest first; ⏳ pending payments are not posted yet.")
            st.dataframe(views.transaction_rows(rows), hide_index=True, width="stretch")
        else:
            st.info("No loans.")
    with calls:
        st.subheader("Calls")
        if history.interactions:
            st.dataframe(views.interaction_rows(history.interactions), hide_index=True, width="stretch")
        else:
            st.info("No calls yet.")
        st.subheader("Open promises")
        if history.open_promises:
            st.dataframe(views.promise_rows(history.open_promises), hide_index=True, width="stretch")
        else:
            st.info("No open promises.")
        st.subheader("Open tasks")
        if history.open_tasks:
            st.dataframe(views.task_rows(history.open_tasks), hide_index=True, width="stretch")
        else:
            st.info("No open tasks.")


selected = st.query_params.get("customer")
if selected and selected.isdigit():
    detail_view(int(selected))
else:
    search_view()
