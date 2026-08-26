# SMCP Kallakudi — Fly Ash Plant Management Web App
## Requirements & Data Model Specification

**Version:** 0.1 (Draft for review)
**Date:** 2026-08-01
**Source documents:** `FlyAshApp.pdf` (requirement note, 2 pages), `JULY.xlsx` (live July 2026 register, 20 sheets)
**Status:** Understanding document. Sections marked 🔶 need the owner's confirmation before build.

---

## 1. Context

SMCP Kallakudi runs a fly ash brick manufacturing unit. Today the entire business — power, labour, production, sales, raw materials, credit — is maintained in a single monthly Excel workbook (`JULY.xlsx`), hand-keyed, with formulas that are re-created every month.

**The problems with the current approach:**

| Problem | Evidence in JULY.xlsx |
|---|---|
| Running balances are typed, not computed | `Sheet5` opening stock is a manual copy of the previous row's balance; only a handful of cells actually carry `=E5-F5` |
| Same entity typed many ways | `NEERAJ` / `NEERAJ  KUMAR` / `NEERAJ . P`; `VEHICLE` names double as customer identity |
| Cross-sheet totals are manual | `Sheet3` extra-wage column is keyed by hand although `Sheet14` holds the underlying qty |
| No history | Each month is a new workbook; only `Sheet16` keeps a 30-month roll-up |
| Single-file, single-user | No concurrent entry, no audit trail, no backup |

**The goal:** a web app where each transaction is entered once, all balances are derived, and the *existing Excel format is still produced* — so the owner's reporting habit, the auditor's expectations, and the printed registers do not change.

---

## 2. Guiding principles

1. **Enter once, derive everything else.** Anything currently computed by subtraction or lookup becomes a derived field the user cannot type.
2. **SQLite is the source of truth. Excel is an artefact.** The workbook is regenerated on demand from the database, never read back as authority. (See §8.)
3. **Excel output must be byte-comparable in layout** to the current sheets — same order, same headers, same "one production row at the end of each day" convention. An accountant opening the exported file should not notice a change.
4. **Masters before transactions.** Free-text names disappear; every labour, customer, product and material is an FK.
5. **Nothing is deleted.** Corrections are edits with an audit row.

---

## 3. Sheet inventory — what actually exists

The requirement note describes 15 sheets. The workbook has 20. The five undescribed sheets are real, in-use registers and are included below.

| Sheet | Title in file | Rows | Described in note? | Nature |
|---|---|---|---|---|
| Sheet1 | Power consumption details | 46 | ✅ Sheet1 | Daily entry, derived deltas |
| Sheet2 | Staff attendance | 40 | ✅ Sheet2 | Daily matrix |
| Sheet3 | Staff wages (per-day income) | 47 | ✅ Sheet3 | **Fully derived** |
| Sheet4 | Staff advance | 24 | ✅ Sheet4 | Ad-hoc entry |
| Sheet5 | Fly ash bricks inventory | 188 | ✅ Sheet5 | Per-sale + daily production |
| Sheet6 | Inventory sales details | 217 | ✅ Sheet6 | Per-sale, with payment mode |
| Sheet7 | Dispatch / delivery register | 50 | ✅ Sheet7 ("debtor") | Delivery note + collection status |
| Sheet8 | 4"×16"×8" solid stock | 36 | ✅ Sheet8 | Same as Sheet5 |
| Sheet9 | 6"×12"×8" solid stock | 18 | ✅ Sheet9 | Same as Sheet5 |
| Sheet10 | Raw materials inward | 29 | ✅ Sheet10 | Wide, one column per material |
| **Sheet11** | **Bike mileage** | **40** | **❌** | Daily odometer |
| Sheet12 | Cement stock log | 38 | ✅ Sheet12 | Derived open/close + lot tracking |
| **Sheet13** | **Customer profile** | **81** | **❌** | Master data (living in a transaction sheet) |
| Sheet14 | Fly ash powder unloading (extra work) | 38 | ✅ Sheet14 | Qty matrix × rate |
| Sheet15 | Salary summary | 21 | ✅ Sheet15 | **Fully derived** |
| **Sheet16** | **Monthly stock summary 2024→2026** | **48** | **❌** | 30-month roll-up |
| **Sheet17** | **Pending collections** | **78** | **❌** | Debtor list (short) |
| **Sheet18** | **Outstanding details** | **143** | **❌** | Debtor ledger (detailed) |
| **Sheet19** | **Digital account ledger** | **39** | **❌** | Bank/UPI in–out |
| Sheet20 | Pallets movement | 67 | ❌ | Pallet stocking/production areas |

> 🔶 **Decision needed:** Sheets 11, 16, 17, 18, 19, 20 — build in Phase 1, or defer? My recommendation is in §10.
> Note that 16, 17, 18 are *derivable* from other sheets and should never be typed again.

---

## 4. Sheet-by-sheet analysis

Legend: **[E]** = entered by user, **[D]** = derived by system, **[M]** = master lookup.

---

### 4.1 Sheet1 — Power Consumption

Meter no. `62190011134`. One row per calendar day.

| Column | Field | Type | Rule |
|---|---|---|---|
| A | Date | [E] | Unique per day |
| B | KWH reading | [E] | Cumulative meter reading |
| C | KVAH reading | [E] | Cumulative meter reading |
| D | Daily KWH | [D] | `KWH(today) − KWH(previous row)` |
| E | Daily KVAH | [D] | `KVAH(today) − KVAH(previous row)` |
| F | Daily PF | [D] | `D / E` |
| G | Cumulative KWH | [D] | `KWH(today) − KWH(EB reading day)` |
| H | Cumulative KVAH | [D] | `KVAH(today) − KVAH(EB reading day)` |
| I | Cumulative PF | [D] | `G / H` |
| J | PF movement | [D] | `I(today) − I(previous row)` — signed |

**The EB reading day.** Row 5 holds `2026-06-16` with readings `57569 / 69767` and no daily figures. This is the billing-cycle anchor: cumulative columns measure from here, not from the 1st of the month. The anchor row must therefore be a **flagged record**, not just "the first row".

- Verified from formulas: `D6 = B6−B5`, `G7 = B7−B5`, `J8 = I8−I7`.
- Note `J` compares **cumulative** PF, not daily PF. The requirement note says "pf increase or decrease from the previous day" — the file implements it on cumulative. **I will follow the file.** 🔶 Confirm.

**Edge cases to handle:**
- Missing day (no reading taken) → the next row's delta silently spans two days. The app must either forbid gaps or store an explicit `days_spanned`.
- Meter rollover / meter replacement → needs a new anchor row.
- PF is a ratio ≤ 1; a value > 1 means a keying error → validate on entry.

---

### 4.2 Sheet2 — Staff Attendance

Wide matrix: one row per date, one column per labour, plus a TOTAL column.

- Values observed: `0`, `0.5`, `1`. Requirement note allows `+0.5 / +1 / −0.5` adjustments, i.e. an effective range of **0 to 2 shift-units in 0.5 steps**.
- 15 labour columns in July. The set changes month to month (`AUGUSTIN`, `PALANI SAMY`, `MANJULA` are all-zero for most of July).
- TOTAL = sum of the row = total shifts worked that day (used as a manpower indicator).

**Storage decision:** the app stores this **long** (one row per labour per date), not wide. The wide grid is a *rendering* concern — both for the entry screen and the Excel export. This is what lets the labour roster change without altering the schema.

**Entry UX:** the note says default is present(1). The screen should pre-fill every active labour with `1` and let the operator only touch exceptions. This is the single highest-frequency screen in the app; it must be one tap per exception and one save.

---

### 4.3 Sheet3 — Staff Per-Day Income — **fully derived, never entered**

For each labour, two columns: `REGULAR DUTY AMT` and `EXTRA DUTY AMT`.

```
regular_amount(labour, date) = attendance_shift_units × per_shift_rate(labour)
extra_amount(labour, date)   = Σ over extra-work entries that day of (qty × rate)
```

Verified: `RAGHUL 2026-07-01` → attendance 1 × ₹500 = ₹500 ✅. Sheet3 row 44 `TOTAL` per column feeds Sheet15.

**A gap that must be resolved.** Sheet14 (fly ash powder unloading) is the *only* extra-work register in the workbook, at ₹100/unit. But:

| Labour | Sheet14 qty × ₹100 | Sheet3 extra total | Unexplained |
|---|---|---|---|
| RAGHUL | 27 × 100 = 2,700 | 6,851 | 4,151 |
| RAKESH | 14 × 100 = 1,400 | 6,971 | 5,571 |
| NEERAJ | 13 × 100 = 1,300 | 5,610 | 4,310 |

So **extra work has more categories than fly ash unloading** — they are being computed on paper and keyed straight into Sheet3. Also note `MANJULA` earns ₹830 extra but has no Sheet14 column at all.

➡️ The app needs an **Extra Work Type master** (name, unit, rate, effective-from date), with Sheet14 becoming just one type. 🔶 **I need the full list of extra work types and their rates.** This is the single biggest unknown in the spec.

**Special marker:** `/` appears in Sheet3 where a labour had not yet joined (e.g. `UGAN`, days 1–2). This must render as `/` in the export but be `NULL` in the database — distinct from `0` (absent).

---

### 4.4 Sheet4 — Staff Advance

Same wide shape as Sheet2, but **sparse** — a row exists only on days an advance was paid. Columns: 15 labour + TOTAL + day-of-week name.

- Stored long: `(date, labour_id, amount, mode, remarks)`.
- July total advances: ₹1,03,000 across 15 dated entries.
- Cross-check available: Sheet19 (digital ledger) records some of the same advances on the expense side — e.g. `RAKESH ADVANCE ₹3,000` on 07-02, `UGAN ADVANCE ₹5,000` on 07-09. 🔶 Should an advance entry optionally post to the digital ledger automatically? Recommend yes, with a `payment_mode` of CASH / DIGITAL.

---

### 4.5 Sheets 5, 8, 9 — Stock Logs (one per product)

Three sheets, identical structure, one per product:

| Sheet | Product | Rate seen |
|---|---|---|
| 5 | FLY ASH BRICKS (FAB) | ₹6 – ₹7.40 |
| 8 | 4"×16"×8" SOLID | ₹35 – ₹40 |
| 9 | 6"×12"×8" SOLID | — |

Row structure per day:
```
row 1..n  : one row per SALE      → S.No, Opening, Prodn=0, Total, Sales, Balance, Vehicle, Consumer
row n+1   : the day's PRODUCTION  → S.No blank, Opening, Prodn, Total, Sales=0, Balance, "NIL", "NIL", per-day-sales-qty
```

Derivations (confirmed from `E5=C5+D5`, `G5=E5−F5`, `C6=E5−F5`):
```
total   = opening + production
balance = total − sales
opening(next row) = balance(previous row)
```
Opening of the first row of the month = closing balance of the previous month.

**Key convention:** production is posted **once, at day end, after all sales**. This is why the production row sits last. The exporter must reproduce this ordering exactly.

Also note:
- Date column uses `"` (ditto mark) for repeat rows within a day. This is a *display* convention, produced by the exporter, never stored.
- The per-day-sales column (J) is only filled on the last row of each day.
- Sheet8 row 10 shows `DAMAGE / SIR` as a "sale" of 16 units. **Damages and internal/own-use issues are being recorded as sales.** ➡️ The app should have a `movement_type` on outward entries: `SALE | DAMAGE | INTERNAL | SAMPLE`, so damages don't inflate revenue. 🔶 Confirm.
- July FAB roll-up (Sheet5 row 188): Opening 64,403 · Production 122,900 · Sales 142,824 · Closing 44,479. These reconcile exactly with Sheet16 row 40. Good — the app must preserve this reconciliation as a hard invariant.

➡️ **These three sheets are one table** with a `product_id`. Adding a fourth product must not require code changes.

---

### 4.6 Sheet6 — Sales Details

One row per sale, keyed alongside Sheet5/8/9 (same event, entered twice today).

| Column | Field | Notes |
|---|---|---|
| A | Date | `"` ditto for repeats |
| B | S.No | Sale sequence within the day |
| C | Inventory name | [M] → product master |
| D | Qty | |
| E | Per rate | Varies by customer/product |
| F | Total amt | [D] `qty × rate` |
| G | Per-day total | [D] only on the last row of a day |
| H | Vehicle items | [M] → vehicle master |
| I | Consumer location | [M] → customer master |
| J,K,L | CASH / CREDIT / DIGITAL A/C | Exactly one is marked |
| M–S | Ad-hoc columns | `P/PAY`, collector name, `A/C`, amount — appears from row 207 |

July totals (row 213): sales ₹10,69,943 · of which digital ₹1,77,094.

**Critical finding: Sheet5 and Sheet6 are the same event.** Sheet5 row 5 (`FAB 500, MKS AUTO, PALINGANATHAM`) and Sheet6 row 5 (`FAB 500 @ ₹6, MKS AUTO, PALINGANATHAM`) are one sale. ➡️ In the app, **a single "Record Sale" action writes one row**, and both Sheet5 and Sheet6 are *views* of it. This alone removes half the daily keying.

The rate column varies within the same product on the same day (₹6 vs ₹7.40 for FAB). ➡️ Rate is per-transaction with a master default, not a fixed price list.

---

### 4.7 Sheet7 — Dispatch / Delivery Register

Titled *"SMCP GREATFUL…"*, 37 entries in July. Columns: S.No, Date, Name, Particulars, Identification (vehicle), Details (`FAB 1000 QTY`), **Amt/Rs**, Exact unload (location or `PENDING RS 13275`), Contact no, Follow (`MD`), Page no, Driver name.

The requirement note calls this "debtor details", and it partly is — the `AMT/RS` column holds `7400` where money is owed and `RECEIVED` where settled. But its real function is a **delivery note / gate pass** with driver and contact, cross-referenced by page number.

July outstanding (row 50): ₹1,09,250.

➡️ This is not a separate register. It is: *sale → delivery → collection status*. Recommend modelling as `deliveries` (linked to a sale) + a `receipts` table, with Sheet7 as a view. 🔶 Confirm the page-no cross-reference is to a physical book and can be a free-text field.

---

### 4.8 Sheets 17 & 18 — Debtors (both undescribed, both derivable)

- **Sheet17 "RIGHT NOW PENDING"** — 6 live debtors, ₹1,12,090 total. Simple list: name, village, contact, amount.
- **Sheet18 "OUT STANDING DETAILS"** — 143 rows, per-transaction with date, amount, pending amount, running total. Contains multi-row customers (`PANIMAI DOSS` twice, `PRABHAKAR` twice) with a grouped total in the last column.

These two plus Sheet7's `AMT/RS` are three overlapping views of the same debt. ➡️ **One `receivables` ledger** (charge on credit sale, credit on receipt), from which all three render. No manual debtor typing.

---

### 4.9 Sheet10 — Raw Materials Inward

Wide format — one column per material type, `---` where not applicable:

| Material | Unit | Rate seen (₹) |
|---|---|---|
| Powder (fly ash) | UNIT | 4,600 |
| Chips | UNIT | 4,600 |
| Ramco Cement | BAGS | 350 |
| M Sand | UNIT | — |
| ½ Jally | UNIT | — |
| Vitala Neo Chemical | LTRS | 35 |
| FAP Mix Cement | BAGS | 150 |

Common columns: Date, From (supplier), Bill No, Vehicle No, Rate, Total Amt, Driver Name, **Status (PAID / …)**.

July totals (row 27): Powder 63.5 · Chips 8.5 · Cement 640 bags · Chemical 350 L · FAP cement 42 bags · Total ₹5,76,600. Rows 22–24 track a supplier advance: ₹1,38,950 advance, ₹66,500 adjusted (19 × ₹3,500), ₹72,450 balance.

➡️ **Normalise to long format.** One `material_receipts` row per (date, supplier, bill, material, qty, rate). The wide grid is an export view. Adding a material becomes a master entry, not a new column.

➡️ Supplier advance/adjustment is a **supplier ledger** — a mirror of the customer receivables ledger. 🔶 Confirm scope.

---

### 4.10 Sheet12 — Cement Stock Log

```
total   = opening + ramco_in + fap_cement_in        (E5 = B5+C5+D5)
closing = total − consumption − sales               (H5 = E5−F5−G5)
```
Confirmed from formulas. Fractional bags occur (`207.5`, `3.5`) — cement is issued in half-bags. **Quantities must be DECIMAL, not INTEGER.**

Columns J–Q are a second block: **closing stock split by receipt lot**, one column per inward date (`2026-06-01`, `2026-05-16`, `2026-07-05`, `2026-07-17`, `2026-07-30`, `2026-07-31`), with `R = J+K+L` reconciling to the closing figure. This is FIFO lot tracking — consumption is drawn from the oldest lot first.

- Column I holds an occasional note (`VIJAY`, `3' U-LID`) — a free-text remark on the consumption.
- ➡️ Lot tracking is a real requirement, not decoration. The app must maintain per-lot balances and deplete FIFO. 🔶 Confirm FIFO (vs. manual lot selection).
- ➡️ Note that cement is *both* consumed (production) and *sold* (column G). It is a raw material and a saleable item.

---

### 4.11 Sheet14 — Extra Work: Fly Ash Powder Unloading

Matrix: date × 9 labour, qty per labour per day. Row 37 gives `TOTAL QTY 99 × RATE 100 = ₹9,900` (`N37 = L37*M37`).

Only 9 of the 15 labour appear here. As established in §4.3, this is one of several extra-work categories.

➡️ Generalised model: `extra_work(date, labour_id, work_type_id, qty)`; amount = `qty × rate(work_type, effective date)`.

---

### 4.12 Sheet15 — Salary Summary — **fully derived**

One row per labour per month. Confirmed formulas: `E4 = C4*D4`, `G4 = E4−F4`, `I4 = E4+H4`.

| Column | Field | Rule |
|---|---|---|
| C | Muster wage | [M] per-shift rate |
| D | Duty | [D] Σ attendance shift-units for the month |
| E | Salary | [D] `C × D` |
| F | Advance | [D] Σ Sheet4 for the month |
| G | Balance | [D] `E − F` |
| H | Extra | [D] Σ Sheet3 extra column |
| I | Total | [D] `E + H` |
| J | Paid on date | [E] |
| K | Authority signature | [E] `M.DIRECTOR` |
| L | Status | [E] |
| M | Pending | [D] |
| N | Outstanding | [E] |

July: total payment ₹2,96,742 · pending ₹9,500.

**Two wage models exist — this is a schema requirement:**

| Type | Labour | Behaviour |
|---|---|---|
| DAILY | RAGHUL…PALANI SAMY (₹500), MANJULA (₹400) | `salary = rate × duty` |
| FIXED ("FIXER") | JEBAMALAI ₹6,000 · PAULRAJ.T ₹12,000 · IYAPPAN ₹12,000 · M.DIRECTOR ₹75,000 | Fixed monthly amount; duty recorded but not multiplied |

Verify: `IYAPPAN` duty 21, salary ₹12,000 → not 21 × anything. Confirmed fixed.

➡️ Labour master needs `wage_type ∈ {DAILY, FIXED}` and a rate field whose meaning depends on it. The requirement note's "Full shift salary" only covers the DAILY case.

---

### 4.13 Sheet13 — Customer Profile (master data, undescribed)

76 customers: Date added, Name, Village, Contact number, Identification (`FAB PARTY`, `BULKER DRIVER`, `APS SOUND SERVICE`, `COMPANY RATION`, `CAME A JOB`).

➡️ This is the **customer master** the app needs — it already exists, just filed as a transaction log. Import it directly. The "Identification" field becomes `customer_type` (a small master) plus a free-text note.

---

### 4.14 Sheet16 — Monthly Stock Summary (undescribed, derivable)

37 months (Jan 2024 → Jul 2026) of FAB: Opening, Production, Total, Sales, Closing — with quarterly subtotal rows interleaved.

July 2026 matches Sheet5 exactly. ➡️ **Pure aggregate. Generate it; never type it.** Extend to all products.

---

### 4.15 Sheet19 — Digital Account Ledger (undescribed)

Two-sided ledger: left = inflow (customer payments, described as `KARUDAMANGALAM FAB 1200 → ₹8,880`), right = outflow (labour advances, fuel, cement purchases).

July: income ₹2,06,967 · expense ₹2,05,860 · **balance ₹1,107**.

➡️ Overlaps Sheet4 (advances) and Sheet6 (digital sales). Should be **auto-posted** from those, with room for manual entries (fuel, misc). Prevents the current triple-keying.

---

### 4.16 Sheet20 — Pallets Movement (undescribed)

Tracks pallets across two physical areas:
`Stocking area · Production area · Today's prodn · Total · Pallets stocked · Shifted from stocking · Prodn area balance · Stocking area balance · Total`

Operational tracking, no financial impact. 🔶 Low priority — confirm.

### 4.17 Sheet11 — Bike Mileage (undescribed)

Date, Start odo, Stop odo, KMs (derived). June carry-forward 444 km. Trivial; include as a small utility register.

---

## 5. Master data required

Drawn from the requirement note plus what the workbook actually needs:

### 5.1 Labour
| Field | Notes |
|---|---|
| name | Canonical; today the same person appears 3 ways |
| phone | Present in Sheet3 row 3 |
| aadhar | From requirement note; not in workbook |
| wage_type | `DAILY` \| `FIXED` — **new, essential** (§4.12) |
| rate | ₹/shift if DAILY, ₹/month if FIXED |
| join_date / exit_date | Drives the `/` marker and the active roster |
| is_active | |

### 5.2 Product / Stock Item
| Field | Notes |
|---|---|
| code, name | `FAB`, `4"×16"×8" SOLID`, `6"×12"×8" SOLID` |
| unit | NOS |
| default_rate | Overridable per sale |
| opening_stock | Per financial period |

### 5.3 Raw Material
Name, unit (`UNIT` / `BAGS` / `LTRS`), default rate, `is_lot_tracked` (true for cement), `is_saleable` (true for cement).

### 5.4 Customer
Name, village, contact, type, opening balance. **Import from Sheet13.**

### 5.5 Supplier
Name, contact, opening advance. Extract from Sheet10 (`SHRI VARI BLUE METAL`, `NAVIIN RAJA`, `BULKER`, …).

### 5.6 Vehicle
`MKS AUTO`, `KRS TRACTOR`, `PARAMESHWARI AUTO`… — currently free text, doubling as customer identity.

### 5.7 Extra Work Type 🔶
Name, unit, rate, effective_from. **Blocked on the owner's list of categories.**

---

## 6. Application structure

The requirement note prescribes **Dashboard → Master → Enter Details → View Details**. Retained, with the undescribed sheets slotted in.

### 6.1 Dashboard
- Current stock per product (with today's production and sales)
- Raw material stock — cement bags (with lot ages), powder, chips, chemical
- Today: production, sales value, cash/credit/digital split, labour present
- Receivables outstanding (Sheet7 + 17 + 18 unified) with an ageing bucket
- Digital account balance
- Power: yesterday's consumption, cumulative, PF trend arrow
- Alerts: cement below reorder level, PF below threshold, credit over limit, missing attendance for today

### 6.2 Master
View / Edit / New for each of §5. Soft delete only.

### 6.3 Enter Details
| # | Screen | Writes to | Notes |
|---|---|---|---|
| 1 | Power consumption | Sheet1 | One reading/day; deltas shown live before save |
| 2 | Attendance | Sheet2 → 3 | Grid, pre-filled `1` |
| 3 | Extra work | Sheet14 → 3 | Per work type |
| 4 | Advance payment | Sheet4 → 15, 19 | |
| 5 | Stock production | Sheet5/8/9 | Day-end, one per product |
| 6 | **Stock sales** | Sheet5/8/9 **+ 6 + 7 + 18 + 19** | **One entry, five sheets** |
| 7 | Raw material inward | Sheet10, 12 | Creates a cement lot |
| 8 | Raw material consumption | Sheet12 | FIFO lot depletion |
| 9 | Receipt / collection | Sheet7, 18, 19 | *Not in the note — required to close the debtor loop* |
| 10 | Pallets | Sheet20 | Optional |
| 11 | Bike mileage | Sheet11 | Optional |

### 6.4 View Details
Salary (Sheet15) · Stock log per product (5/8/9) · Sales register (6) · Debtors (7/17/18) · Raw material register (10) · Cement log (12) · Power (1) · Monthly summary (16) · Digital ledger (19) — each with a date-range filter and an **Export to Excel** button.

---

## 7. SQLite schema (first cut)

```sql
-- ========== MASTERS ==========
CREATE TABLE labour (
  id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, phone TEXT, aadhar TEXT,
  wage_type TEXT NOT NULL CHECK (wage_type IN ('DAILY','FIXED')),
  rate REAL NOT NULL, join_date DATE, exit_date DATE,
  is_active INTEGER DEFAULT 1, display_order INTEGER);

CREATE TABLE product (
  id INTEGER PRIMARY KEY, code TEXT UNIQUE, name TEXT NOT NULL,
  unit TEXT DEFAULT 'NOS', default_rate REAL, is_active INTEGER DEFAULT 1);

CREATE TABLE raw_material (
  id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, unit TEXT NOT NULL,
  default_rate REAL, is_lot_tracked INTEGER DEFAULT 0,
  is_saleable INTEGER DEFAULT 0, reorder_level REAL);

CREATE TABLE customer (
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, village TEXT, contact TEXT,
  customer_type TEXT, credit_limit REAL, opening_balance REAL DEFAULT 0);

CREATE TABLE supplier (
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, contact TEXT, opening_advance REAL DEFAULT 0);

CREATE TABLE vehicle (
  id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, driver_name TEXT, contact TEXT);

CREATE TABLE extra_work_type (
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, unit TEXT, rate REAL NOT NULL,
  effective_from DATE NOT NULL, is_active INTEGER DEFAULT 1);

-- ========== POWER ==========
CREATE TABLE power_reading (
  id INTEGER PRIMARY KEY, reading_date DATE NOT NULL UNIQUE,
  kwh REAL NOT NULL, kvah REAL NOT NULL,
  is_eb_anchor INTEGER DEFAULT 0,          -- the "EB reading day"
  remarks TEXT);
-- daily/cumulative/PF are computed in a view, never stored

-- ========== LABOUR TRANSACTIONS ==========
CREATE TABLE attendance (
  id INTEGER PRIMARY KEY, att_date DATE NOT NULL, labour_id INTEGER REFERENCES labour(id),
  shift_units REAL NOT NULL CHECK (shift_units BETWEEN 0 AND 2),
  remarks TEXT, UNIQUE(att_date, labour_id));

CREATE TABLE extra_work (
  id INTEGER PRIMARY KEY, work_date DATE NOT NULL, labour_id INTEGER REFERENCES labour(id),
  work_type_id INTEGER REFERENCES extra_work_type(id),
  qty REAL NOT NULL, rate REAL NOT NULL, remarks TEXT);  -- rate snapshotted

CREATE TABLE advance (
  id INTEGER PRIMARY KEY, adv_date DATE NOT NULL, labour_id INTEGER REFERENCES labour(id),
  amount REAL NOT NULL, payment_mode TEXT CHECK (payment_mode IN ('CASH','DIGITAL')),
  remarks TEXT);

CREATE TABLE salary_settlement (            -- only the non-derived part of Sheet15
  id INTEGER PRIMARY KEY, period_year INTEGER, period_month INTEGER,
  labour_id INTEGER REFERENCES labour(id), paid_on DATE, authority TEXT,
  status TEXT, outstanding REAL DEFAULT 0,
  UNIQUE(period_year, period_month, labour_id));

-- ========== STOCK ==========
CREATE TABLE production (
  id INTEGER PRIMARY KEY, prod_date DATE NOT NULL, product_id INTEGER REFERENCES product(id),
  qty REAL NOT NULL, remarks TEXT, UNIQUE(prod_date, product_id));

CREATE TABLE sale (
  id INTEGER PRIMARY KEY, sale_date DATE NOT NULL, seq_no INTEGER,
  product_id INTEGER REFERENCES product(id), qty REAL NOT NULL, rate REAL NOT NULL,
  amount REAL GENERATED ALWAYS AS (qty * rate) STORED,
  movement_type TEXT DEFAULT 'SALE'
    CHECK (movement_type IN ('SALE','DAMAGE','INTERNAL','SAMPLE')),
  customer_id INTEGER REFERENCES customer(id), vehicle_id INTEGER REFERENCES vehicle(id),
  payment_mode TEXT CHECK (payment_mode IN ('CASH','CREDIT','DIGITAL')),
  delivery_location TEXT, driver_name TEXT, page_no TEXT, remarks TEXT);

CREATE TABLE stock_opening (
  id INTEGER PRIMARY KEY, product_id INTEGER REFERENCES product(id),
  as_on DATE NOT NULL, qty REAL NOT NULL, UNIQUE(product_id, as_on));

-- ========== RAW MATERIALS ==========
CREATE TABLE material_receipt (
  id INTEGER PRIMARY KEY, receipt_date DATE NOT NULL,
  supplier_id INTEGER REFERENCES supplier(id), bill_no TEXT, vehicle_no TEXT,
  material_id INTEGER REFERENCES raw_material(id), qty REAL NOT NULL, rate REAL NOT NULL,
  amount REAL GENERATED ALWAYS AS (qty * rate) STORED,
  driver_name TEXT, payment_status TEXT DEFAULT 'PENDING');

CREATE TABLE material_lot (                  -- cement FIFO
  id INTEGER PRIMARY KEY, receipt_id INTEGER REFERENCES material_receipt(id),
  material_id INTEGER REFERENCES raw_material(id),
  lot_date DATE NOT NULL, qty_in REAL NOT NULL, qty_remaining REAL NOT NULL);

CREATE TABLE material_consumption (
  id INTEGER PRIMARY KEY, cons_date DATE NOT NULL,
  material_id INTEGER REFERENCES raw_material(id), qty REAL NOT NULL,
  lot_id INTEGER REFERENCES material_lot(id), remarks TEXT);

-- ========== MONEY ==========
CREATE TABLE receipt (                       -- customer collections
  id INTEGER PRIMARY KEY, receipt_date DATE NOT NULL,
  customer_id INTEGER REFERENCES customer(id), sale_id INTEGER REFERENCES sale(id),
  amount REAL NOT NULL, payment_mode TEXT, collected_by TEXT, remarks TEXT);

CREATE TABLE digital_ledger (
  id INTEGER PRIMARY KEY, txn_date DATE NOT NULL,
  direction TEXT CHECK (direction IN ('IN','OUT')),
  description TEXT, amount REAL NOT NULL,
  source_table TEXT, source_id INTEGER);     -- auto-posted vs manual

-- ========== MISC REGISTERS ==========
CREATE TABLE pallet_movement (
  id INTEGER PRIMARY KEY, move_date DATE UNIQUE, stocking_area REAL, production_area REAL,
  today_prodn REAL, pallets_stocked REAL, shifted_from_stocking REAL);

CREATE TABLE bike_mileage (
  id INTEGER PRIMARY KEY, log_date DATE UNIQUE, start_km REAL, stop_km REAL);

-- ========== AUDIT ==========
CREATE TABLE audit_log (
  id INTEGER PRIMARY KEY, table_name TEXT, record_id INTEGER,
  action TEXT, old_value TEXT, new_value TEXT,
  changed_by TEXT, changed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
```

**Design notes**
- Rates are **snapshotted** onto `extra_work` and `sale`. Changing a master rate must never retroactively alter a past month's salary or invoice.
- No stored running balances anywhere. `opening`, `total`, `balance`, cumulative power, monthly summaries — all SQL views. This is what removes the current class of copy-paste errors.
- `movement_type` on `sale` separates damages from revenue while keeping the Sheet5 layout intact.
- Every quantity is `REAL` — half-bags of cement and half-shifts of attendance both occur.

---

## 8. Dual persistence: SQLite + Excel

**The rule: SQLite writes are authoritative; Excel is generated output.**

```
User entry ──► SQLite (transactional, validated)
                 │
                 └──► Excel exporter ──► JULY.xlsx (identical layout)
```

Why not write both simultaneously: a mid-write crash would leave the two out of sync with no way to tell which is right. Regenerating from the database is always safe and always reproducible.

**Exporter requirements**
1. One `.xlsx` per month, sheets in the current order with the current titles (`Sheet1`…`Sheet20`) 🔶 — *or* should sheets be renamed to something readable? The current names carry no meaning.
2. Reproduce the display conventions: `"` ditto marks, `/` for not-yet-joined, `---` for N/A materials, `NIL` for empty vehicle/consumer.
3. Write **live formulas**, not just values, so the file remains a working Excel register if opened standalone.
4. Preserve merged headers, column widths and the multi-row header blocks (Sheet3 uses rows 2–8 as a header).
5. Optional import path for the first migration only, so July 2026 history is not re-keyed.

**Suggested stack** (🔶 open to preference)

| Layer | Choice | Reason |
|---|---|---|
| Backend | Python + FastAPI | `openpyxl` is the strongest Excel writer available |
| DB | SQLite + SQLAlchemy | Single file, trivial backup, matches the requirement |
| Excel | `openpyxl` | Formulas, merges, styling, formats |
| Frontend | React + Vite, or server-rendered HTMX | HTMX is materially simpler for a grid-entry app |
| Deployment | Single machine on the plant LAN, nightly file backup | No internet dependency at the plant |

---

## 9. Validation & business rules

| # | Rule | Severity |
|---|---|---|
| 1 | Sale qty ≤ available stock (opening + production − prior sales) | Block |
| 2 | Meter reading ≥ previous reading | Block |
| 3 | PF must be ≤ 1 | Block |
| 4 | Attendance shift_units ∈ {0, 0.5, 1, 1.5, 2} | Block |
| 5 | One production entry per product per day | Block |
| 6 | Material consumption ≤ available lot balance | Block |
| 7 | Advance total for a month ≤ earned salary + extra | Warn |
| 8 | Credit sale pushing a customer over their limit | Warn |
| 9 | Attendance missing for a past date | Warn on dashboard |
| 10 | Cement below reorder level | Alert |
| 11 | Editing a locked (settled) month | Block, unless overridden with a reason |
| 12 | Deleting a sale that has a receipt against it | Block |

**Month-end close.** Once salary is settled and stock rolled forward, the month should lock: closing balances become the next month's opening, and further edits require an explicit reopen with an audit entry.

---

## 10. Recommended build order

| Phase | Scope | Rationale |
|---|---|---|
| **1** | Masters · Attendance · Extra work · Advance · Salary (Sheets 2,3,4,14,15) | Highest keying burden, fully derivable, self-contained |
| **2** | Products · Production · Sales (Sheets 5,6,8,9,16) | The "enter once, five sheets" win |
| **3** | Customers · Deliveries · Receivables (Sheets 7,13,17,18) | Closes the money loop |
| **4** | Raw materials · Cement lots (Sheets 10,12) | Most complex derivation |
| **5** | Power · Digital ledger · Dashboard (Sheets 1,19 + dashboard) | |
| **6** | Pallets · Bike mileage (Sheets 11,20) | Nice-to-have |

Each phase ships with its Excel exporter, so the workbook stays complete throughout.

---

## 11. Open questions 🔶

**Blocking**
1. **Extra work types.** What are the full categories and rates beyond fly ash powder unloading (₹100/unit)? ~₹4,000–5,500 per labour per month is currently unaccounted for.
2. **Damages.** Sheet8 records 16 units as `DAMAGE / SIR`. Should damages be a separate movement type excluded from sales revenue?
3. **Cement lots.** Is consumption strictly FIFO, or does the operator choose the lot?

**Important**
4. PF movement (Sheet1 col J) — the file compares *cumulative* PF; the note says *daily*. Which is intended?
5. Sheet7 "page no" — a physical book reference? Free text acceptable?
6. Should labour advances auto-post to the digital ledger when paid digitally?
7. Should supplier advances (Sheet10 rows 22–24) get a full supplier ledger?
8. Multi-user? How many people will enter data, and does the app need login and roles?

**Minor**
9. Sheet names — keep `Sheet1…Sheet20` for familiarity, or rename meaningfully?
10. Sheets 11 (bike) and 20 (pallets) — in scope?
11. Financial year boundary — April–March or January–December for the Sheet16 roll-up?
12. Is Aadhar to be stored? It carries a legal obligation to secure it; recommend storing only the last 4 digits unless it's genuinely needed.

---

## 12. Summary of what changes

| Today | With the app |
|---|---|
| A sale is keyed into 4–5 sheets | Entered once |
| Balances typed by hand | Computed, never typed |
| Extra wages computed on paper | Derived from qty × rate |
| Debtors tracked in 3 places | One receivables ledger |
| Same person named 3 ways | One master record |
| New month = new workbook | Continuous history, month-end close |
| Excel is the system | Excel is a report the system prints |