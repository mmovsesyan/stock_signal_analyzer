"""add commands json to daily_usage and user_alerts table

Revision ID: 20260527_0006
Revises: 20260523_0005
Create Date: 2026-05-27 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "20260527_0006"
down_revision = "20260523_0005"
branch_labels = None
depends_on = None


def upgrade():
    # daily_usage.commands JSON column
    op.add_column(
        "daily_usage",
        sa.Column("commands", postgresql.JSON(astext_type=sa.Text()), server_default="{}", nullable=True),
    )
    # user_alerts table
    op.create_table(
        "user_alerts",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.Column("alert_type", sa.String(length=20), nullable=False),
        sa.Column("condition", sa.String(length=20), nullable=False),
        sa.Column("threshold", sa.Float(), nullable=True),
        sa.Column("target_tier", sa.String(length=5), nullable=True),
        sa.Column("target_direction", sa.String(length=10), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=True),
        sa.Column("last_triggered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cooldown_sec", sa.Integer(), server_default="3600", nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_user_alerts_user_id", "user_alerts", ["user_id"], unique=False)


def downgrade():
    op.drop_index("ix_user_alerts_user_id", table_name="user_alerts")
    op.drop_table("user_alerts")
    op.drop_column("daily_usage", "commands")
