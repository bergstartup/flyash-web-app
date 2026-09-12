"""stock month history, and a destination for damaged stock

Revision ID: c2f8b30d5a41
Revises: b1c4e7a92f10
Create Date: 2026-09-12

Sheet16 rolls fly ash brick stock up over 30 months, of which the app owns
exactly one -- the other 29 closed before the database existed and cannot be
derived from movements that were never recorded. They are imported history,
like ``stock_opening``, and the writer prefers a derived month wherever it
has one.

The damage importer also dropped the name the damage was booked against
(Sheet8 records 16 units as DAMAGE / SIR), keeping it only inside a remarks
string. It belongs in ``destination`` like any other outward movement.
"""
from alembic import op
import sqlalchemy as sa

revision = "c2f8b30d5a41"
down_revision = "b1c4e7a92f10"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "stock_month_history",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("product_id", sa.Integer(), sa.ForeignKey("product.id"),
                  nullable=False),
        sa.Column("period_year", sa.Integer(), nullable=False),
        sa.Column("period_month", sa.Integer(), nullable=False),
        sa.Column("opening", sa.Float(), nullable=False),
        sa.Column("production", sa.Float(), nullable=False),
        sa.Column("sales", sa.Float(), nullable=False),
        sa.Column("closing", sa.Float(), nullable=False),
        sa.UniqueConstraint("product_id", "period_year", "period_month"),
    )
    # 'Sheet8: recorded as a sale to SIR' -> destination 'SIR'.
    op.get_bind().execute(sa.text("""
        UPDATE sale
           SET destination = TRIM(SUBSTR(remarks, INSTR(remarks, ' to ') + 4))
         WHERE movement_type <> 'SALE'
           AND destination IS NULL
           AND remarks LIKE '%: recorded as a sale to %'
    """))


def downgrade() -> None:
    op.drop_table("stock_month_history")
    op.get_bind().execute(sa.text(
        "UPDATE sale SET destination = NULL WHERE movement_type <> 'SALE'"))
