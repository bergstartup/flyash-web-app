"""date the stock arriving into a cement lot

Revision ID: f4b8c1e07d93
Revises: e3d7f21a9c05
Create Date: 2026-09-12

``material_lot.qty_in`` held everything that ever entered a lot, with no date
against any of it, so ``lot_balance_daily`` showed every lot full from the
first of the month. Sheet12 says otherwise: the "M / N" bucket takes stock on
eight separate days and the 31-bag CON bucket only exists on the 31st.

Worse, a lot that grew was counted twice -- once into ``qty_in`` and again as
a negative allocation -- which is why the 5 Jul lot of 80 bags read 160.

Inflows now carry their own date. ``qty_in`` goes: it was a stored total of
things the database can add up, which is the one thing this schema does not do.
"""
from alembic import op
import sqlalchemy as sa

revision = "f4b8c1e07d93"
down_revision = "e3d7f21a9c05"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "material_lot_inflow",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("lot_id", sa.Integer(), sa.ForeignKey("material_lot.id"),
                  nullable=False),
        sa.Column("receipt_id", sa.Integer(),
                  sa.ForeignKey("material_receipt.id"), nullable=True),
        sa.Column("as_on", sa.Date(), nullable=False),
        sa.Column("qty", sa.Float(), nullable=False),
    )
    op.create_index("ix_material_lot_inflow_as_on", "material_lot_inflow",
                    ["as_on"])
    # Carry every existing lot across as a single inflow on its own date, so a
    # database that is not re-imported still balances. The July import records
    # each arrival separately.
    op.get_bind().execute(sa.text("""
        INSERT INTO material_lot_inflow (lot_id, as_on, qty)
        SELECT ml.id,
               COALESCE(ml.lot_date, (SELECT MIN(as_on) FROM material_opening)),
               ml.qty_in
        FROM material_lot ml WHERE ml.qty_in <> 0
    """))
    # SQLite validates every view against the table before letting a column
    # go, and both of these read qty_in. They are rebuilt from views.sql after
    # each migration, so dropping them here costs nothing.
    for view in ("lot_balance_daily", "lot_balance"):
        op.get_bind().exec_driver_sql(f"DROP VIEW IF EXISTS {view}")
    # Dropped natively rather than through a batch recreate: recreating the
    # table would drop and rebuild it, and consumption_allocation points at it.
    op.get_bind().exec_driver_sql(
        "ALTER TABLE material_lot DROP COLUMN qty_in")


def downgrade() -> None:
    op.get_bind().exec_driver_sql(
        "ALTER TABLE material_lot ADD COLUMN qty_in FLOAT NOT NULL DEFAULT 0")
    op.get_bind().execute(sa.text("""
        UPDATE material_lot SET qty_in = COALESCE(
            (SELECT SUM(qty) FROM material_lot_inflow i WHERE i.lot_id = material_lot.id), 0)
    """))
    op.drop_index("ix_material_lot_inflow_as_on", "material_lot_inflow")
    op.drop_table("material_lot_inflow")
