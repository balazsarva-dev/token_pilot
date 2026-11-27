"""m2 sync period: the days an API sync covered, on its import batch

Revision ID: 3c1f9b2a7d4e
Revises: 8e53f77a386e
Create Date: 2026-10-20 18:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "3c1f9b2a7d4e"
down_revision: str | None = "8e53f77a386e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("import_batch", sa.Column("period_start", sa.Date(), nullable=True))
    op.add_column("import_batch", sa.Column("period_end", sa.Date(), nullable=True))


def downgrade() -> None:
    op.drop_column("import_batch", "period_end")
    op.drop_column("import_batch", "period_start")
