"""Baseline for the kb database (empty; tables arrive with T-KB-02).

Revision ID: kb_0001
Revises:
Create Date: 2026-11-06
"""

from collections.abc import Sequence

revision: str = "kb_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
