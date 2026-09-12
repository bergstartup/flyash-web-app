# SMCP Kallakudi — fly ash plant register

Replaces the monthly Excel workbook (`JULY.xlsx`, 20 sheets, 1,373 hand-kept
formulas) with a SQLite database that derives every balance, and regenerates
the workbook as a report.

**Phase 0 is complete.** See `docs/` — the build plan artifact — for the seven
phases and the fifteen schema decisions.

## Guiding rule

Nothing that is currently produced by subtraction, lookup or a hand-run total
is ever an input field. There is no `opening`, `balance`, `total` or
`qty_remaining` column anywhere in the schema; those live in `app/db/views.sql`.

## Running

```bash
python3 -m venv .venv && ./.venv/bin/pip install -r requirements.txt
./.venv/bin/alembic upgrade head        # schema + views
./.venv/bin/python scripts/import_july.py   # migrate JULY.xlsx (destructive)
./scripts/run.sh                        # serve on 0.0.0.0:8000
```

## The three checks that matter

```bash
./.venv/bin/python scripts/reconcile.py     # 32 July figures, derived vs workbook
./.venv/bin/python scripts/anomalies.py     # the error register, from the raw file
./.venv/bin/python scripts/export_check.py  # regenerate July, diff cell by cell
./.venv/bin/python -m pytest tests -q       # all of the above, as tests
```

`scripts/build_template.py` re-lifts `app/excel/template.xlsx` from the source
workbook — headers, merges, widths and styles kept, data cells cleared. Run it
only if the register's layout changes.

## Backups

`scripts/backup.sh` takes a `VACUUM INTO` snapshot (safe against a live WAL
database, which a file copy is not), keeps 30, and copies the generated
workbook alongside. `scripts/restore.sh <snapshot>` verifies integrity, moves
the current database aside, and restores. **Rehearse the restore on the plant
machine** — an untested backup is not a backup.

Suggested crontab on the plant box:

```
30 22 * * *  ./scripts/backup.sh >> data/backup.log 2>&1
```

## Layout

| Path | Holds |
|---|---|
| `app/db/models.py` | 33 tables. No derived columns. |
| `app/db/views.sql` | Every derivation. Rebuilt after each migration. |
| `app/services/` | Write paths — one module per entry screen. |
| `app/reports/` | Read paths — one function per sheet, shared by the screens and the Excel writers. |
| `app/excel/` | Template, toolkit, per-sheet writers, diff harness. |
| `app/importer/` | One-time July migration and the anomaly detector. |

## Authentication

There is none, by decision: the app runs on the plant LAN. Every write passes
through `app/web/actor.py::current_actor`, which stamps `audit_log.changed_by`
from a name dropdown. Adding real logins means implementing that one function.

## Excel export

Sheet names stay `Sheet1…Sheet20` — the owner's habit and the auditor's
expectation both key off them. Where the register's own formulas are
inconsistent by hand, the app emits correct consistent ones and the diff
harness whitelists the difference with a reason (`app/excel/diff.py`).

Writers implemented: **Sheet2, Sheet3, Sheet4, Sheet13, Sheet14, Sheet15** --
the whole labour block plus the customer master. The other 14 are reported as
`NOT IMPLEMENTED` by the diff harness rather than silently passing, and land
in Phases 2–6.
