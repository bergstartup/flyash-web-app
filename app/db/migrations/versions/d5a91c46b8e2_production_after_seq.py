"""record where the day's production falls among its sales

Revision ID: d5a91c46b8e2
Revises: c2f8b30d5a41
Create Date: 2026-09-12

The stock ledgers post production at the point in the day it happened, not at
the end -- Sheet5 puts 2 July's 3,700 bricks between the third and fourth sale
-- and every opening balance below that point depends on it. Without the
position the app can only guess, and guessing wrong walks the whole month's
running balance off the register.

NULL means "posted last", which is what a new entry defaults to.
"""
from alembic import op
import sqlalchemy as sa

revision = "d5a91c46b8e2"
down_revision = "c2f8b30d5a41"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("production") as batch:
        batch.add_column(sa.Column("after_seq", sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("production") as batch:
        batch.drop_column("after_seq")
