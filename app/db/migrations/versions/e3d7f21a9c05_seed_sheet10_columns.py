"""record which Sheet10 column each raw material occupies

Revision ID: e3d7f21a9c05
Revises: d5a91c46b8e2
Create Date: 2026-09-12

Sheet10 is wide: one column per material. The column belongs on the material,
so a writer never has to carry a list of names -- but the July import left the
field empty. A fresh import now fills it; this backfills the seven that were
loaded from the sheet, in the order the sheet lists them.
"""
from alembic import op
import sqlalchemy as sa

revision = "e3d7f21a9c05"
down_revision = "d5a91c46b8e2"
branch_labels = None
depends_on = None

#: Sheet10's own column order, which is also the materials' display_order.
COLUMNS = ["E", "F", "G", "H", "I", "J", "K"]


def upgrade() -> None:
    conn = op.get_bind()
    for order, letter in enumerate(COLUMNS):
        conn.execute(sa.text(
            "UPDATE raw_material SET sheet10_column = :c "
            "WHERE display_order = :o AND sheet10_column IS NULL"),
            {"c": letter, "o": order})


def downgrade() -> None:
    op.get_bind().execute(sa.text(
        "UPDATE raw_material SET sheet10_column = NULL"))
