"""Alembic environment for the `kb` database. Run: `uv run alembic -n kb upgrade head`."""

from supportagent.common.db import KbBase
from supportagent.common.migrate import run_env

# Import the KB's model modules here so autogenerate sees their tables (T-KB-02), e.g.:
#   import supportagent.kb.models

run_env("kb", KbBase.metadata)
