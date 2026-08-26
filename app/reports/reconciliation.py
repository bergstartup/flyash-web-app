"""The July 2026 correctness oracle.

Every figure here was read out of JULY.xlsx directly, not taken from the
requirement document -- several of the spec's numbers were wrong. The app must
reproduce each one from derived data with nothing typed.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import text

# label -> (expected value, SQL returning a single number)
EXPECTED: dict[str, tuple[float, str]] = {
    # -- Phase 1: labour ------------------------------------------------
    "Sheet2  total shifts": (374.5, "SELECT SUM(shift_units) FROM attendance"),
    # Sheet3 prints 145,800. Attendance x rate is 145,400 -- the 400 rupee
    # gap is MANJULA, error 5 in the register. The app derives the correct one.
    "Sheet3  regular wages (corrected)": (145400.0, """
        SELECT SUM(shift_units * l.rate) FROM attendance a
        JOIN labour l ON l.id = a.labour_id WHERE l.wage_type = 'DAILY'"""),
    "Sheet3  extra wages": (46342.0, "SELECT SUM(qty * rate) FROM extra_work"),
    "Sheet14 unloading value": (9900.0, """
        SELECT SUM(e.qty * e.rate) FROM extra_work e
        JOIN extra_work_type t ON t.id = e.work_type_id
        WHERE t.is_adjustment = 0"""),
    "Sheet14 unloading qty": (99.0, """
        SELECT SUM(e.qty) FROM extra_work e
        JOIN extra_work_type t ON t.id = e.work_type_id
        WHERE t.is_adjustment = 0"""),
    "        unexplained extra": (36442.0, """
        SELECT SUM(e.qty * e.rate) FROM extra_work e
        JOIN extra_work_type t ON t.id = e.work_type_id
        WHERE t.is_adjustment = 1"""),
    "Sheet4  dated advances": (154500.0, "SELECT SUM(amount) FROM advance"),
    "Sheet15 total payment": (296742.0,
                              "SELECT SUM(total_payment) FROM salary_month"),
    "Sheet15 pending": (9500.0, "SELECT SUM(outstanding) FROM salary_month"),

    # -- Phase 2: stock and sales ---------------------------------------
    "Sheet5  FAB opening": (64403.0, """
        SELECT opening FROM stock_month_summary s JOIN product p ON p.id = s.product_id
        WHERE p.ledger_sheet = 'Sheet5'"""),
    "Sheet5  FAB production": (122900.0, """
        SELECT production FROM stock_month_summary s JOIN product p ON p.id = s.product_id
        WHERE p.ledger_sheet = 'Sheet5'"""),
    "Sheet5  FAB sales": (142824.0, """
        SELECT sales FROM stock_month_summary s JOIN product p ON p.id = s.product_id
        WHERE p.ledger_sheet = 'Sheet5'"""),
    "Sheet5  FAB closing": (44479.0, """
        SELECT closing FROM stock_month_summary s JOIN product p ON p.id = s.product_id
        WHERE p.ledger_sheet = 'Sheet5'"""),
    "Sheet8  4in opening": (452.0, """
        SELECT opening FROM stock_month_summary s JOIN product p ON p.id = s.product_id
        WHERE p.ledger_sheet = 'Sheet8'"""),
    "Sheet8  4in production": (5078.0, """
        SELECT production FROM stock_month_summary s JOIN product p ON p.id = s.product_id
        WHERE p.ledger_sheet = 'Sheet8'"""),
    "Sheet9  6in production": (2387.0, """
        SELECT production FROM stock_month_summary s JOIN product p ON p.id = s.product_id
        WHERE p.ledger_sheet = 'Sheet9'"""),
    "Sheet9  6in closing (corrected)": (2287.0, """
        SELECT closing FROM stock_month_summary s JOIN product p ON p.id = s.product_id
        WHERE p.ledger_sheet = 'Sheet9'"""),
    # Sheet8 prints 3,172 sold. Sheet6 has 3,606 plus a 16-unit damage that
    # never reached Sheet6: the app derives 3,622, and the 450-unit gap is
    # error 2 in the register.
    "Sheet8  4in sales (corrected)": (3622.0, """
        SELECT sales FROM stock_month_summary s JOIN product p ON p.id = s.product_id
        WHERE p.ledger_sheet = 'Sheet8'"""),
    "Sheet8  4in closing (corrected)": (1908.0, """
        SELECT closing FROM stock_month_summary s JOIN product p ON p.id = s.product_id
        WHERE p.ledger_sheet = 'Sheet8'"""),
    "Sheet6  sales total": (1069943.0, "SELECT SUM(amount) FROM sales_register"),
    "Sheet6  distinct products": (15.0, "SELECT COUNT(*) FROM product"),
    "Sheet6  value with no ledger": (71290.0, """
        SELECT SUM(r.amount) FROM sales_register r
        JOIN product p ON p.id = r.product_id WHERE p.has_stock_ledger = 0"""),
    "Sheet6  amount overrides": (5.0,
                                 "SELECT COUNT(*) FROM sale_line WHERE amount_overridden = 1"),

    # -- Phase 4: raw materials -----------------------------------------
    "Sheet10 materials total": (576600.0, "SELECT SUM(amount) FROM material_receipt"),
    "Sheet10 powder qty": (63.5, """
        SELECT SUM(qty) FROM material_receipt r JOIN raw_material m ON m.id = r.material_id
        WHERE m.name = 'POWDER'"""),
    "Sheet10 ramco cement bags": (640.0, """
        SELECT SUM(qty) FROM material_receipt r JOIN raw_material m ON m.id = r.material_id
        WHERE m.name = 'RAMCO CEMENT'"""),
    "Sheet12 cement consumed": (528.5, """
        SELECT SUM(qty) FROM material_consumption c
        JOIN raw_material m ON m.id = c.material_id WHERE m.name = 'CEMENT'"""),

    # -- Phase 3: receivables -------------------------------------------
    # Sheet17's own grand total. Which of the four registers is right is the
    # owner's call (anomaly A08); what the app must not do is invent a fifth.
    "Sheet17 receivables snapshot": (432883.0,
                                     "SELECT SUM(outstanding) FROM receivable_balance"),

    # -- Phase 5: digital ledger ----------------------------------------
    "Sheet19 opening balance": (14273.0, """
        SELECT SUM(amount) FROM digital_ledger WHERE entry_type = 'OPENING'"""),
    "Sheet19 income (corrected)": (192694.0, """
        SELECT SUM(amount) FROM digital_ledger
        WHERE direction = 'IN' AND entry_type <> 'OPENING'"""),
    "Sheet19 expense": (205860.0, """
        SELECT SUM(amount) FROM digital_ledger WHERE direction = 'OUT'"""),
    "Sheet19 closing balance": (1107.0, "SELECT closing FROM digital_ledger_month"),
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
        value = conn.execute(text(sql)).scalar()
        out.append(Measurement(label, expected, float(value or 0.0)))
    return out
