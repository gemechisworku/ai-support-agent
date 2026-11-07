"""T-CON-01 · CON-R1/R7: the Streamlit shell renders with the synthetic-data banner, without network."""

import pytest

from supportagent.console import views
from tests.console.conftest import CONSOLE, FakeApi

pytest.importorskip("streamlit")


def test_shell_renders(fake_api: FakeApi) -> None:
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(str(CONSOLE / "app.py"), default_timeout=30).run()
    assert not at.exception
    assert any(views.BANNER in w.value for w in at.warning)
    assert any("Support agent console" in t.value for t in at.title)
