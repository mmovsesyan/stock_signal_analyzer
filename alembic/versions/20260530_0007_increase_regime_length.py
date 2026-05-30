"""increase regime and weekly_regime length

Revision ID: 20260530_0007
Revises: 20260527_0006
Create Date: 2026-05-30 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "20260530_0007"
down_revision = "20260527_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("signals", "regime", type_=sa.String(100), existing_type=sa.String(30))
    op.alter_column("signals", "weekly_regime", type_=sa.String(100), existing_type=sa.String(30))


def downgrade() -> None:
    op.alter_column("signals", "weekly_regime", type_=sa.String(30), existing_type=sa.String(100))
    op.alter_column("signals", "regime", type_=sa.String(30), existing_type=sa.String(100))
