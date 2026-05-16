"""initial

Revision ID: 20260516_0001
Revises:
Create Date: 2026-05-16 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "20260516_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create all tables from ORM metadata
    import sys
    import os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    from stock_signal_analyzer.db import Base
    Base.metadata.create_all(bind=op.get_bind())


def downgrade() -> None:
    import sys
    import os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    from stock_signal_analyzer.db import Base
    Base.metadata.drop_all(bind=op.get_bind())
