-- Every derivation in the system. Nothing here is ever stored.
-- Statements are split on ";\n" by app/db/session.py, so keep one blank-line
-- separated statement per block and do not put ";\n" inside a statement.

DROP VIEW IF EXISTS stock_movement;
CREATE VIEW stock_movement AS
    -- Sales in entry order within the day ...
    SELECT
        l.product_id                       AS product_id,
        s.sale_date                        AS d,
        0                                  AS kind,
        s.seq_no                           AS seq,
        l.line_no                          AS line_no,
        0.0                                AS produced,
        l.qty                              AS issued,
        s.movement_type                    AS movement_type,
        s.id                               AS sale_id,
        l.id                               AS sale_line_id,
        NULL                               AS production_id,
        s.vehicle_id                       AS vehicle_id,
        s.destination                      AS destination
    FROM sale s
    JOIN sale_line l ON l.sale_id = s.id
    UNION ALL
    -- ... then the day's production, at the point in the day it was posted:
    -- after the sale whose seq_no it carries, or last when it carries none.
    SELECT
        p.product_id, p.prod_date, 1, COALESCE(p.after_seq, 1000000), 0,
        p.qty, 0.0, 'PRODUCTION', NULL, NULL, p.id, NULL, NULL
    FROM production p;

DROP VIEW IF EXISTS stock_ledger;
CREATE VIEW stock_ledger AS
SELECT
    m.product_id,
    m.d                                                    AS movement_date,
    date(m.d, 'start of month')                            AS period_start,
    m.kind,
    m.seq,
    m.line_no,
    m.movement_type,
    m.sale_id,
    m.sale_line_id,
    m.production_id,
    m.vehicle_id,
    m.destination,
    m.produced,
    m.issued,
    COALESCE(o.qty, 0)
        + SUM(m.produced - m.issued) OVER w
        - (m.produced - m.issued)                          AS opening,
    COALESCE(o.qty, 0)
        + SUM(m.produced - m.issued) OVER w
        - (m.produced - m.issued) + m.produced             AS total,
    COALESCE(o.qty, 0) + SUM(m.produced - m.issued) OVER w AS balance
FROM stock_movement m
LEFT JOIN stock_opening o
       ON o.product_id = m.product_id
      AND o.as_on = date(m.d, 'start of month')
WINDOW w AS (
    PARTITION BY m.product_id, date(m.d, 'start of month')
    ORDER BY m.d, m.seq, m.kind, m.line_no
    ROWS UNBOUNDED PRECEDING
);

DROP VIEW IF EXISTS stock_month_summary;
CREATE VIEW stock_month_summary AS
SELECT
    m.product_id,
    date(m.d, 'start of month')            AS period_start,
    COALESCE(o.qty, 0)                     AS opening,
    SUM(m.produced)                        AS production,
    COALESCE(o.qty, 0) + SUM(m.produced)   AS total,
    SUM(m.issued)                          AS sales,
    COALESCE(o.qty, 0) + SUM(m.produced) - SUM(m.issued) AS closing
FROM stock_movement m
LEFT JOIN stock_opening o
       ON o.product_id = m.product_id
      AND o.as_on = date(m.d, 'start of month')
GROUP BY m.product_id, date(m.d, 'start of month'), o.qty;

DROP VIEW IF EXISTS sales_register;
CREATE VIEW sales_register AS
SELECT
    s.id                AS sale_id,
    s.sale_date,
    s.seq_no,
    l.line_no,
    l.product_id,
    p.name              AS product_name,
    l.qty,
    l.rate,
    l.amount,
    l.amount_overridden,
    s.movement_type,
    s.payment_mode,
    s.destination,
    s.customer_id,
    s.vehicle_id,
    v.name              AS vehicle_name
FROM sale s
JOIN sale_line l ON l.sale_id = s.id
JOIN product p   ON p.id = l.product_id
LEFT JOIN vehicle v ON v.id = s.vehicle_id
WHERE s.movement_type = 'SALE';

DROP VIEW IF EXISTS power_daily;
CREATE VIEW power_daily AS
SELECT
    r.reading_date,
    r.kwh,
    r.kvah,
    r.kwh  - LAG(r.kwh)  OVER w                        AS daily_kwh,
    r.kvah - LAG(r.kvah) OVER w                        AS daily_kvah,
    CASE WHEN r.kvah - LAG(r.kvah) OVER w <> 0
         THEN (r.kwh - LAG(r.kwh) OVER w)
            / (r.kvah - LAG(r.kvah) OVER w) END        AS daily_pf,
    CAST(julianday(r.reading_date)
         - julianday(LAG(r.reading_date) OVER w) AS INTEGER) AS days_spanned,
    r.kwh  - a.anchor_kwh                              AS cum_kwh,
    r.kvah - a.anchor_kvah                             AS cum_kvah,
    CASE WHEN r.kvah - a.anchor_kvah <> 0
         THEN (r.kwh - a.anchor_kwh)
            / (r.kvah - a.anchor_kvah) END             AS cum_pf,
    a.anchor_date
FROM power_reading r
JOIN (
    SELECT
        x.id,
        (SELECT k.reading_date FROM power_reading k
          WHERE k.is_eb_anchor = 1 AND k.reading_date <= x.reading_date
          ORDER BY k.reading_date DESC, k.id DESC LIMIT 1) AS anchor_date,
        (SELECT k.kwh FROM power_reading k
          WHERE k.is_eb_anchor = 1 AND k.reading_date <= x.reading_date
          ORDER BY k.reading_date DESC, k.id DESC LIMIT 1) AS anchor_kwh,
        (SELECT k.kvah FROM power_reading k
          WHERE k.is_eb_anchor = 1 AND k.reading_date <= x.reading_date
          ORDER BY k.reading_date DESC, k.id DESC LIMIT 1) AS anchor_kvah
    FROM power_reading x
) a ON a.id = r.id
WHERE r.reading_taken = 1 AND r.is_eb_anchor = 0
WINDOW w AS (ORDER BY r.reading_date);

DROP VIEW IF EXISTS extra_work_daily;
CREATE VIEW extra_work_daily AS
SELECT
    e.work_date,
    e.labour_id,
    SUM(e.qty * e.rate) AS extra_amount,
    SUM(CASE WHEN t.is_adjustment = 1 THEN e.qty * e.rate ELSE 0 END)
        AS adjustment_amount
FROM extra_work e
JOIN extra_work_type t ON t.id = e.work_type_id
GROUP BY e.work_date, e.labour_id;

DROP VIEW IF EXISTS wage_daily;
CREATE VIEW wage_daily AS
SELECT
    a.att_date,
    a.labour_id,
    l.name                AS labour_name,
    l.wage_type,
    a.shift_units,
    CASE WHEN l.wage_type = 'DAILY' THEN a.shift_units * l.rate END
                          AS regular_amount,
    COALESCE(x.extra_amount, 0.0) AS extra_amount
FROM attendance a
JOIN labour l ON l.id = a.labour_id
LEFT JOIN extra_work_daily x
       ON x.labour_id = a.labour_id AND x.work_date = a.att_date;

DROP VIEW IF EXISTS salary_month;
CREATE VIEW salary_month AS
WITH periods AS (
    SELECT DISTINCT CAST(strftime('%Y', att_date) AS INTEGER) AS y,
                    CAST(strftime('%m', att_date) AS INTEGER) AS mth
    FROM attendance
    UNION
    SELECT period_year, period_month FROM salary_settlement
),
att AS (
    SELECT labour_id,
           CAST(strftime('%Y', att_date) AS INTEGER) AS y,
           CAST(strftime('%m', att_date) AS INTEGER) AS mth,
           SUM(shift_units) AS duty
    FROM attendance GROUP BY labour_id, y, mth
),
adv AS (
    SELECT labour_id,
           CAST(strftime('%Y', adv_date) AS INTEGER) AS y,
           CAST(strftime('%m', adv_date) AS INTEGER) AS mth,
           SUM(amount) AS total
    FROM advance GROUP BY labour_id, y, mth
),
xw AS (
    SELECT labour_id,
           CAST(strftime('%Y', work_date) AS INTEGER) AS y,
           CAST(strftime('%m', work_date) AS INTEGER) AS mth,
           SUM(qty * rate) AS total
    FROM extra_work GROUP BY labour_id, y, mth
)
SELECT
    p.y                                     AS period_year,
    p.mth                                   AS period_month,
    l.id                                    AS labour_id,
    l.name                                  AS labour_name,
    l.wage_type,
    l.rate                                  AS muster_wage,
    -- C15: M.DIRECTOR has duty in Sheet15 and no attendance column anywhere,
    -- so a FIXED labour may carry an explicit duty_override instead.
    COALESCE(ss.duty_override, att.duty, 0.0) AS duty,
    CASE WHEN l.wage_type = 'DAILY'
         THEN COALESCE(att.duty, 0.0) * l.rate
         ELSE l.rate END                    AS salary,
    COALESCE(adv.total, 0.0)                AS advance,
    CASE WHEN l.wage_type = 'DAILY'
         THEN COALESCE(att.duty, 0.0) * l.rate
         ELSE l.rate END - COALESCE(adv.total, 0.0) AS balance,
    COALESCE(xw.total, 0.0)                 AS extra,
    CASE WHEN l.wage_type = 'DAILY'
         THEN COALESCE(att.duty, 0.0) * l.rate
         ELSE l.rate END + COALESCE(xw.total, 0.0) AS total_payment,
    ss.paid_on,
    ss.authority,
    ss.status,
    COALESCE(ss.outstanding, 0.0)           AS outstanding
FROM labour l
CROSS JOIN periods p
LEFT JOIN att ON att.labour_id = l.id AND att.y = p.y AND att.mth = p.mth
LEFT JOIN adv ON adv.labour_id = l.id AND adv.y = p.y AND adv.mth = p.mth
LEFT JOIN xw  ON xw.labour_id  = l.id AND xw.y  = p.y AND xw.mth  = p.mth
LEFT JOIN salary_settlement ss
       ON ss.labour_id = l.id
      AND ss.period_year = p.y AND ss.period_month = p.mth
WHERE att.duty IS NOT NULL OR ss.id IS NOT NULL OR adv.total IS NOT NULL;

DROP VIEW IF EXISTS lot_balance;
CREATE VIEW lot_balance AS
SELECT
    ml.id            AS lot_id,
    ml.material_id,
    ml.label,
    ml.lot_date,
    ml.display_order,
    COALESCE((SELECT SUM(i.qty) FROM material_lot_inflow i
               WHERE i.lot_id = ml.id), 0.0)     AS qty_in,
    COALESCE((SELECT SUM(i.qty) FROM material_lot_inflow i
               WHERE i.lot_id = ml.id), 0.0)
      - COALESCE((SELECT SUM(ca.qty) FROM consumption_allocation ca
                   WHERE ca.lot_id = ml.id), 0.0) AS qty_remaining
FROM material_lot ml;

DROP VIEW IF EXISTS lot_balance_daily;
CREATE VIEW lot_balance_daily AS
-- What is left of each lot at the end of each day. A lot fills over several
-- days -- Sheet12's "M / N" bucket takes stock on eight of them -- so only
-- the inflows that have actually arrived by the day are counted.
SELECT
    d.day            AS cons_date,
    ml.id            AS lot_id,
    ml.material_id,
    ml.label,
    ml.display_order,
    COALESCE((
        SELECT SUM(i.qty) FROM material_lot_inflow i
        WHERE i.lot_id = ml.id AND i.as_on <= d.day
    ), 0.0)
      - COALESCE((
            SELECT SUM(ca.qty)
            FROM consumption_allocation ca
            JOIN material_consumption mc ON mc.id = ca.consumption_id
            WHERE ca.lot_id = ml.id AND mc.cons_date <= d.day
        ), 0.0)        AS qty_remaining
FROM (
    SELECT DISTINCT cons_date AS day FROM material_consumption
    UNION SELECT DISTINCT as_on FROM material_lot_inflow
) d
CROSS JOIN material_lot ml;

DROP VIEW IF EXISTS material_daily;
CREATE VIEW material_daily AS
SELECT
    d.day                                   AS cons_date,
    d.material_id,
    COALESCE(r.received, 0.0)               AS received,
    COALESCE(c.consumed, 0.0)               AS consumed,
    COALESCE(s.sold, 0.0)                   AS sold
FROM (
    SELECT DISTINCT receipt_date AS day, material_id FROM material_receipt
    UNION
    SELECT DISTINCT cons_date, material_id FROM material_consumption
) d
LEFT JOIN (
    SELECT receipt_date AS day, material_id, SUM(qty) AS received
    FROM material_receipt GROUP BY day, material_id
) r ON r.day = d.day AND r.material_id = d.material_id
LEFT JOIN (
    SELECT cons_date AS day, material_id, SUM(qty) AS consumed
    FROM material_consumption GROUP BY day, material_id
) c ON c.day = d.day AND c.material_id = d.material_id
LEFT JOIN (
    SELECT s2.sale_date AS day, rm.id AS material_id, SUM(l.qty) AS sold
    FROM sale s2
    JOIN sale_line l ON l.sale_id = s2.id
    JOIN product p   ON p.id = l.product_id
    JOIN raw_material rm ON UPPER(rm.name) = UPPER(p.name)
    WHERE rm.is_saleable = 1
    GROUP BY day, rm.id
) s ON s.day = d.day AND s.material_id = d.material_id;

DROP VIEW IF EXISTS receivable_entry;
CREATE VIEW receivable_entry AS
    SELECT
        ro.customer_id, ro.as_on AS entry_date, 'OPENING' AS kind,
        ro.amount AS charge, 0.0 AS credit, NULL AS sale_id, ro.id AS source_id
    FROM receivable_opening ro
    UNION ALL
    SELECT
        s.customer_id, s.sale_date, 'SALE',
        (SELECT SUM(l.amount) FROM sale_line l WHERE l.sale_id = s.id),
        0.0, s.id, s.id
    FROM sale s
    -- C11: a credit sale with no identifiable customer cannot join the
    -- ledger. Those 15 sales are reported as anomaly A14 instead.
    WHERE s.payment_mode = 'CREDIT' AND s.movement_type = 'SALE'
      AND s.customer_id IS NOT NULL
    UNION ALL
    SELECT
        rc.customer_id, rc.receipt_date, 'RECEIPT',
        0.0, rc.amount, rc.sale_id, rc.id
    FROM receipt rc;

DROP VIEW IF EXISTS receivable_balance;
CREATE VIEW receivable_balance AS
WITH anchor AS (
    SELECT customer_id, MAX(as_on) AS as_on
    FROM receivable_opening GROUP BY customer_id
)
SELECT
    e.customer_id,
    SUM(e.charge)                 AS total_charged,
    SUM(e.credit)                 AS total_received,
    SUM(e.charge) - SUM(e.credit) AS outstanding
FROM receivable_entry e
LEFT JOIN anchor a ON a.customer_id = e.customer_id
-- An opening balance supersedes everything dated before it: the snapshot
-- already contains those charges.
WHERE a.as_on IS NULL OR e.kind = 'OPENING' OR e.entry_date >= a.as_on
GROUP BY e.customer_id;

DROP VIEW IF EXISTS digital_ledger_month;
CREATE VIEW digital_ledger_month AS
SELECT
    date(txn_date, 'start of month') AS period_start,
    SUM(CASE WHEN entry_type = 'OPENING' THEN amount ELSE 0 END) AS opening,
    SUM(CASE WHEN direction = 'IN'  AND entry_type <> 'OPENING'
             THEN amount ELSE 0 END) AS income,
    SUM(CASE WHEN direction = 'OUT' THEN amount ELSE 0 END) AS expense,
    SUM(CASE WHEN direction = 'IN'  THEN amount ELSE -amount END) AS closing
FROM digital_ledger
GROUP BY date(txn_date, 'start of month');

DROP VIEW IF EXISTS bike_mileage_daily;
CREATE VIEW bike_mileage_daily AS
SELECT log_date, start_km, stop_km, stop_km - start_km AS kms
FROM bike_mileage;
