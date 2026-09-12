"""Sheet5 -- fly ash bricks inventory.

The widest of the three ledgers: it numbers each day's sales and carries the
day's total quantity on the day's last row.
"""

from __future__ import annotations

from app.excel.writers.stockledger import StockLedgerWriter


class Sheet05Writer(StockLedgerWriter):
    sheet = "Sheet5"
    footer_row = 188

    date_col = 1
    seq_col = 2
    opening_col = 3
    prodn_col = 4
    total_col = 5
    sales_col = 6
    balance_col = 7
    vehicle_col = 8
    consumer_col = 9
    perday_col = 10
