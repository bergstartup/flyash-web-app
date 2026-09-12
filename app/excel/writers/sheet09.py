"""Sheet9 -- 6" x 12" x 8" solid bricks inventory.

The register reserves seven blank rows above its DESCRIPTION row, so the footer
here sits well below the data rather than three rows under it.
"""

from __future__ import annotations

from app.excel.writers.stockledger import StockLedgerWriter


class Sheet09Writer(StockLedgerWriter):
    sheet = "Sheet9"
    footer_row = 18
