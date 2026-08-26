"""Lift the export template out of the source workbook.

Everything that makes the register look like itself -- the header blocks, the
86 merged ranges, column widths, the single Calibri 11 bold face, the four
number formats, the fills and 723 borders -- is kept. Only the data cell
*values* are cleared, so the writers fill a shaped, styled shell.
"""

from __future__ import annotations

from pathlib import Path

import openpyxl

#: sheet -> the first row that holds data rather than headings
FIRST_DATA_ROW = {
    "Sheet1": 5, "Sheet2": 4, "Sheet3": 10, "Sheet4": 4, "Sheet5": 5,
    "Sheet6": 5, "Sheet7": 4, "Sheet8": 5, "Sheet9": 5, "Sheet10": 6,
    "Sheet11": 5, "Sheet12": 5, "Sheet13": 5, "Sheet14": 5, "Sheet15": 4,
    "Sheet16": 6, "Sheet17": 4, "Sheet18": 4, "Sheet19": 5, "Sheet20": 6,
}


def build(source: Path, destination: Path) -> dict[str, int]:
    wb = openpyxl.load_workbook(str(source))
    cleared: dict[str, int] = {}
    for ws in wb.worksheets:
        first = FIRST_DATA_ROW.get(ws.title, 2)
        n = 0
        for row in ws.iter_rows(min_row=first):
            for cell in row:
                if cell.value is not None:
                    cell.value = None
                    n += 1
        cleared[ws.title] = n
    destination.parent.mkdir(parents=True, exist_ok=True)
    wb.save(str(destination))
    return cleared
