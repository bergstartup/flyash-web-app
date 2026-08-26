"""add labour display_order

Revision ID: f8d093683286
Revises: 85072cba4655
Create Date: 2026-08-26
"""
from alembic import op
import sqlalchemy as sa

revision = "f8d093683286"
down_revision = "85072cba4655"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("labour") as batch:
        batch.add_column(
            sa.Column("display_order", sa.Integer(), nullable=False, server_default="0")
        )
    conn = op.get_bind()
    # Backfill from the Sheet2 historical order where it exists, so imported
    # labour keep the order they already had; anyone without one (new hires,
    # or labour absent from Sheet2 such as the FIXED staff) is appended after,
    # ordered by id for stability.
    ordered = conn.execute(sa.text("""
        SELECT l.id FROM labour l
        LEFT JOIN labour_sheet_order o
               ON o.labour_id = l.id AND o.sheet = 'Sheet2'
        ORDER BY CASE WHEN o.position IS NULL THEN 1 ELSE 0 END,
                 o.position, l.id
    """)).scalars().all()
    for i, labour_id in enumerate(ordered):
        conn.execute(sa.text(
            "UPDATE labour SET display_order = :o WHERE id = :i"),
            {"o": i, "i": labour_id})


def downgrade() -> None:
    with op.batch_alter_table("labour") as batch:
        batch.drop_column("display_order")
