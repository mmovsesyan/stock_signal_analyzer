"""add last_bot_msg_id to users

Revision ID: 20260523_0004
Revises: 20260523_0003
Create Date: 2026-05-23 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "20260523_0004"
down_revision = "20260523_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("last_bot_msg_id", sa.Integer(), nullable=False, server_default="0"))


def downgrade() -> None:
    op.drop_column("users", "last_bot_msg_id")
