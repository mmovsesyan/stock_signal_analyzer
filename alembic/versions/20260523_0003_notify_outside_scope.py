"""add notify_outside_scope to users

Revision ID: 20260523_0003
Revises: 20260519_0002
Create Date: 2026-05-23 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "20260523_0003"
down_revision = "20260519_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("notify_outside_scope", sa.String(10), nullable=False, server_default="all"))


def downgrade() -> None:
    op.drop_column("users", "notify_outside_scope")
