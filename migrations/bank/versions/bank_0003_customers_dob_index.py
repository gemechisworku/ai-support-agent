"""index customers.dob for the identity match (T-BANK-08)

Revision ID: bank_0003
Revises: bank_0002
Create Date: 2026-11-07 09:00:00
"""

from collections.abc import Sequence

from alembic import op

revision: str = "bank_0003"
down_revision: str | None = "bank_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(op.f("ix_customers_dob"), "customers", ["dob"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_customers_dob"), table_name="customers")
