"""Alembic environment for the `bank` database. Run: `uv run alembic -n bank upgrade head`."""

from supportagent.common.db import BankBase
from supportagent.common.migrate import run_env

# Import the bank's model modules here so autogenerate sees their tables (T-BANK-01), e.g.:
#   import supportagent.bank.models

run_env("bank", BankBase.metadata)
