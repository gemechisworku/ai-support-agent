"""Operations console and data explorer (specs/11-console).

Run with `make console` (compose service `console`, http://localhost:8501) or, with uv installed,
`uv run streamlit run src/supportagent/console/app.py`. It needs the bank API and the gateway running.
"""

from pathlib import Path

import streamlit as st

from supportagent.console import ui

PAGES = Path(__file__).parent / "pages"

st.set_page_config(page_title="Support agent console", page_icon="🏦", layout="wide")
ui.banner()


def _campaigns() -> None:
    ui.coming_soon(
        "Campaigns", "T-CON-02 (M4)", "run pre-due and missed-payment campaigns, place single calls."
    )


def _calls() -> None:
    ui.coming_soon("Calls", "T-CON-03 (M4)", "live and past calls with transcripts, tool calls and outcomes.")


def _followups() -> None:
    ui.coming_soon("Follow-ups", "T-CON-04 (M4)", "promises, callbacks and escalations to work.")


def _compliance() -> None:
    ui.coming_soon("Compliance", "T-CON-05 (M4)", "verification, disclosure, window and cap metrics.")


navigation = st.navigation(
    {
        "Data explorer": [
            st.Page(PAGES / "home.py", title="Overview", icon="🏠", default=True),
            st.Page(PAGES / "customers.py", title="Customers", icon="👤", url_path="customers"),
            st.Page(PAGES / "policies.py", title="Policies", icon="📚", url_path="policies"),
            st.Page(PAGES / "personas.py", title="Test personas", icon="🎭", url_path="personas"),
            st.Page(PAGES / "audit.py", title="Audit trail", icon="🧾", url_path="audit"),
        ],
        "Operations (M4)": [
            st.Page(_campaigns, title="Campaigns", icon="📣", url_path="campaigns"),
            st.Page(_calls, title="Calls", icon="📞", url_path="calls"),
            st.Page(_followups, title="Follow-ups", icon="📋", url_path="followups"),
            st.Page(_compliance, title="Compliance", icon="✅", url_path="compliance"),
        ],
    }
)
navigation.run()
