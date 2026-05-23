"""add last_bot_msg_has_reply_kb to users

Revision ID: 20260523_0005
Revises: 20260523_0004
Create Date: 2026-05-23 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "20260523_0005"
down_revision = "20260523_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("last_bot_msg_has_reply_kb", sa.Boolean(), nullable=False, server_default="false"))


def downgrade() -> None:
    op.drop_column("users", "last_bot_msg_has_reply_kb")
