"""record what is standing in each pallet area

Revision ID: a7e2c5f4b810
Revises: f4b8c1e07d93
Create Date: 2026-09-12

Sheet20's two BALANCE columns are counted, not calculated: 28 July has 4
pallets in the production area and 3 in stocking against 4 made that day. They
cannot be derived from what is stocked and shifted, so they are recorded.
"""
from alembic import op
import sqlalchemy as sa

revision = "a7e2c5f4b810"
down_revision = "f4b8c1e07d93"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("pallet_movement") as batch:
        batch.add_column(sa.Column("production_balance", sa.Float(), nullable=True))
        batch.add_column(sa.Column("stocking_balance", sa.Float(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("pallet_movement") as batch:
        batch.drop_column("stocking_balance")
        batch.drop_column("production_balance")
