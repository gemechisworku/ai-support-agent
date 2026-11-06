"""Alembic environment for the `bank` database. Run: `uv run alembic -n bank upgrade head`."""

import supportagent.bank.models  # noqa: F401  (registers the bank tables on BankBase.metadata)
from supportagent.common.db import BankBase
from supportagent.common.migrate import run_env

run_env("bank", BankBase.metadata)
