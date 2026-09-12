"""Sheet8 -- 4" x 16" x 8" solid bricks inventory."""

from __future__ import annotations

from app.excel.writers.stockledger import StockLedgerWriter


class Sheet08Writer(StockLedgerWriter):
    sheet = "Sheet8"
    footer_row = 36
