"""seed the Sheet14 column order

Revision ID: b1c4e7a92f10
Revises: f8d093683286
Create Date: 2026-09-12

The July import recorded a per-sheet column order for Sheets 2, 3, 4 and 15
but not for Sheet14, so its writer had no roster to lay out. Sheet14's nine
QTY columns (C..K) are, verified against the source workbook, Sheet4's first
nine columns in the same order and under the same labels -- RAGHUL, RAKESH,
NEERAJ, GOLU, NEERAJ.P, SANTHOSH, UGAN, BIRU, VIKRAM -- so the order is
derived from Sheet4 rather than re-typed here. A fresh import reads it from
the workbook directly.
"""
from alembic import op
import sqlalchemy as sa

revision = "b1c4e7a92f10"
down_revision = "f8d093683286"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    existing = conn.execute(sa.text(
        "SELECT COUNT(*) FROM labour_sheet_order WHERE sheet = 'Sheet14'"
    )).scalar()
    if existing:
        return
    conn.execute(sa.text("""
        INSERT INTO labour_sheet_order (sheet, labour_id, position, label)
        SELECT 'Sheet14', labour_id, position, label
        FROM labour_sheet_order
        WHERE sheet = 'Sheet4' AND position < 9
    """))


def downgrade() -> None:
    op.get_bind().execute(sa.text(
        "DELETE FROM labour_sheet_order WHERE sheet = 'Sheet14'"))
