"""Small Streamlit helpers shared by the console pages."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import streamlit as st

from supportagent.console.clients import ConsoleApiError
from supportagent.console.views import BANNER


def banner() -> None:
    st.warning(BANNER, icon="🧪")


def api[T](fn: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    """Call a client method; on failure show the error (never a traceback) and stop this page run."""
    try:
        return fn(*args, **kwargs)
    except ConsoleApiError as exc:
        st.error(str(exc))
        st.stop()
        raise  # unreachable: st.stop() raises; keeps type checkers happy


def coming_soon(title: str, task: str, what: str) -> None:
    st.title(title)
    st.info(f"Arrives with {task}: {what}")
