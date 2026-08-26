"""The July 2026 correctness oracle.

Every figure here was read out of JULY.xlsx directly, not taken from the
requirement document -- several of the spec's numbers were wrong. The app must
reproduce each one from derived data with nothing typed.

Every query below is explicitly scoped to July 2026. That scoping is not
decorative: once Phase 1 lets the operator enter real transactions for other
months, an unscoped ``SUM(amount) FROM advance`` stops meaning "July's
advances" and starts meaning "every advance ever entered" -- correct only by
accident, for as long as July happened to be the only month in the database.
The one deliberate exception is the receivables balance, which is a live
running total by design, not a monthly figure.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import text

_Y, _M = "2026", "07"          # the oracle's fixed period
JULY = {"y": _Y, "m": _M}

# label -> (expected value, SQL returning a single number, needs date params)
EXPECTED: dict[str, tuple[float, str]] = {
    # -- Phase 1: labour ------------------------------------------------
    "Sheet2  total shifts": (374.5, """
        SELECT SUM(shift_units) FROM attendance
        WHERE strftime('%Y', att_date) = :y AND strftime('%m', att_date) = :m"""),
    "Sheet3  regular wages (corrected)": (145400.0, """
        SELECT SUM(shift_units * l.rate) FROM attendance a
        JOIN labour l ON l.id = a.labour_id
        WHERE l.wage_type = 'DAILY'
          AND strftime('%Y', a.att_date) = :y AND strftime('%m', a.att_date) = :m"""),
    "Sheet3  extra wages": (46342.0, """
        SELECT SUM(qty * rate) FROM extra_work
        WHERE strftime('%Y', work_date) = :y AND strftime('%m', work_date) = :m"""),
    "Sheet14 unloading value": (9900.0, """
        SELECT SUM(e.qty * e.rate) FROM extra_work e
        JOIN extra_work_type t ON t.id = e.work_type_id
        WHERE t.is_adjustment = 0
          AND strftime('%Y', e.work_date) = :y AND strftime('%m', e.work_date) = :m"""),
    "Sheet14 unloading qty": (99.0, """
        SELECT SUM(e.qty) FROM extra_work e
        JOIN extra_work_type t ON t.id = e.work_type_id
        WHERE t.is_adjustment = 0
          AND strftime('%Y', e.work_date) = :y AND strftime('%m', e.work_date) = :m"""),
    "        unexplained extra": (36442.0, """
        SELECT SUM(e.qty * e.rate) FROM extra_work e
        JOIN extra_work_type t ON t.id = e.work_type_id
        WHERE t.is_adjustment = 1
          AND strftime('%Y', e.work_date) = :y AND strftime('%m', e.work_date) = :m"""),
    "Sheet4  dated advances": (154500.0, """
        SELECT SUM(amount) FROM advance
        WHERE strftime('%Y', adv_date) = :y AND strftime('%m', adv_date) = :m"""),
    "Sheet15 total payment": (296742.0,
                              "SELECT SUM(total_payment) FROM salary_month "
                              "WHERE period_year = :y AND period_month = :m"),
    "Sheet15 pending": (9500.0,
                        "SELECT SUM(outstanding) FROM salary_month "
                        "WHERE period_year = :y AND period_month = :m"),

    # -- Phase 2: stock and sales ---------------------------------------
    "Sheet5  FAB opening": (64403.0, """
        SELECT opening FROM stock_month_summary s JOIN product p ON p.id = s.product_id
        WHERE p.ledger_sheet = 'Sheet5' AND s.period_start = :period"""),
    "Sheet5  FAB production": (122900.0, """
        SELECT production FROM stock_month_summary s JOIN product p ON p.id = s.product_id
        WHERE p.ledger_sheet = 'Sheet5' AND s.period_start = :period"""),
    "Sheet5  FAB sales": (142824.0, """
        SELECT sales FROM stock_month_summary s JOIN product p ON p.id = s.product_id
        WHERE p.ledger_sheet = 'Sheet5' AND s.period_start = :period"""),
    "Sheet5  FAB closing": (44479.0, """
        SELECT closing FROM stock_month_summary s JOIN product p ON p.id = s.product_id
        WHERE p.ledger_sheet = 'Sheet5' AND s.period_start = :period"""),
    "Sheet8  4in opening": (452.0, """
        SELECT opening FROM stock_month_summary s JOIN product p ON p.id = s.product_id
        WHERE p.ledger_sheet = 'Sheet8' AND s.period_start = :period"""),
    "Sheet8  4in production": (5078.0, """
        SELECT production FROM stock_month_summary s JOIN product p ON p.id = s.product_id
        WHERE p.ledger_sheet = 'Sheet8' AND s.period_start = :period"""),
    "Sheet8  4in sales (corrected)": (3622.0, """
        SELECT sales FROM stock_month_summary s JOIN product p ON p.id = s.product_id
        WHERE p.ledger_sheet = 'Sheet8' AND s.period_start = :period"""),
    "Sheet8  4in closing (corrected)": (1908.0, """
        SELECT closing FROM stock_month_summary s JOIN product p ON p.id = s.product_id
        WHERE p.ledger_sheet = 'Sheet8' AND s.period_start = :period"""),
    "Sheet9  6in production": (2387.0, """
        SELECT production FROM stock_month_summary s JOIN product p ON p.id = s.product_id
        WHERE p.ledger_sheet = 'Sheet9' AND s.period_start = :period"""),
    "Sheet9  6in closing (corrected)": (2287.0, """
        SELECT closing FROM stock_month_summary s JOIN product p ON p.id = s.product_id
        WHERE p.ledger_sheet = 'Sheet9' AND s.period_start = :period"""),
    "Sheet6  sales total": (1069943.0, """
        SELECT SUM(amount) FROM sales_register
        WHERE strftime('%Y', sale_date) = :y AND strftime('%m', sale_date) = :m"""),
    "Sheet6  distinct products": (15.0, "SELECT COUNT(*) FROM product"),
    "Sheet6  value with no ledger": (71290.0, """
        SELECT SUM(r.amount) FROM sales_register r
        JOIN product p ON p.id = r.product_id WHERE p.has_stock_ledger = 0
          AND strftime('%Y', r.sale_date) = :y AND strftime('%m', r.sale_date) = :m"""),
    "Sheet6  amount overrides": (5.0, """
        SELECT COUNT(*) FROM sale_line l JOIN sale s ON s.id = l.sale_id
        WHERE l.amount_overridden = 1
          AND strftime('%Y', s.sale_date) = :y AND strftime('%m', s.sale_date) = :m"""),

    # -- Phase 3: receivables -------------------------------------------
    # Sheet17's own grand total. Which of the four registers is right is the
    # owner's call (anomaly A08); what the app must not do is invent a fifth.
    # Deliberately unscoped by date: this is a live running balance, not a
    # monthly figure.
    "Sheet17 receivables snapshot": (432883.0,
                                     "SELECT SUM(outstanding) FROM receivable_balance"),

    # -- Phase 4: raw materials -----------------------------------------
    "Sheet10 materials total": (576600.0, """
        SELECT SUM(amount) FROM material_receipt
        WHERE strftime('%Y', receipt_date) = :y AND strftime('%m', receipt_date) = :m"""),
    "Sheet10 powder qty": (63.5, """
        SELECT SUM(qty) FROM material_receipt r JOIN raw_material m ON m.id = r.material_id
        WHERE m.name = 'POWDER'
          AND strftime('%Y', r.receipt_date) = :y AND strftime('%m', r.receipt_date) = :m"""),
    "Sheet10 ramco cement bags": (640.0, """
        SELECT SUM(qty) FROM material_receipt r JOIN raw_material m ON m.id = r.material_id
        WHERE m.name = 'RAMCO CEMENT'
          AND strftime('%Y', r.receipt_date) = :y AND strftime('%m', r.receipt_date) = :m"""),
    "Sheet12 cement consumed": (528.5, """
        SELECT SUM(qty) FROM material_consumption c
        JOIN raw_material m ON m.id = c.material_id WHERE m.name = 'CEMENT'
          AND strftime('%Y', c.cons_date) = :y AND strftime('%m', c.cons_date) = :m"""),

    # -- Phase 5: digital ledger ----------------------------------------
    "Sheet19 opening balance": (14273.0, """
        SELECT SUM(amount) FROM digital_ledger WHERE entry_type = 'OPENING'
          AND strftime('%Y', txn_date) = :y AND strftime('%m', txn_date) = :m"""),
    "Sheet19 income (corrected)": (192694.0, """
        SELECT SUM(amount) FROM digital_ledger
        WHERE direction = 'IN' AND entry_type <> 'OPENING'
          AND strftime('%Y', txn_date) = :y AND strftime('%m', txn_date) = :m"""),
    "Sheet19 expense": (205860.0, """
        SELECT SUM(amount) FROM digital_ledger WHERE direction = 'OUT'
          AND strftime('%Y', txn_date) = :y AND strftime('%m', txn_date) = :m"""),
    "Sheet19 closing balance": (1107.0,
                                "SELECT closing FROM digital_ledger_month "
                                "WHERE period_start = :period"),
}


@dataclass
class Measurement:
    label: str
    expected: float
    actual: float

    @property
    def matches(self) -> bool:
        return abs(self.actual - self.expected) < 0.01


def measure(conn) -> list[Measurement]:
    out = []
    for label, (expected, sql) in EXPECTED.items():
        value = conn.execute(text(sql), {
            "y": _Y, "m": _M, "period": f"{_Y}-{_M}-01"}).scalar()
        out.append(Measurement(label, expected, float(value or 0.0)))
    return out
