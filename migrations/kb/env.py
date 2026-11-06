"""Alembic environment for the `kb` database. Run: `uv run alembic -n kb upgrade head`."""

import supportagent.kb.models  # noqa: F401  (registers documents/chunks on KbBase.metadata)
from supportagent.common.db import KbBase
from supportagent.common.migrate import run_env

run_env("kb", KbBase.metadata)
