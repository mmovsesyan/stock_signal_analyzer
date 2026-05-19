"""add per-market usage counters

Revision ID: 20260519_0002
Revises: 20260516_0001
Create Date: 2026-05-19 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "20260519_0002"
down_revision = "20260516_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("daily_usage", sa.Column("us_count", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("daily_usage", sa.Column("ru_count", sa.Integer(), nullable=False, server_default="0"))


def downgrade() -> None:
    op.drop_column("daily_usage", "ru_count")
    op.drop_column("daily_usage", "us_count")
