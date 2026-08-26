"""Strip JULY.xlsx down to a styled, empty template."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import EXCEL_TEMPLATE, SOURCE_WORKBOOK
from app.excel.template import build

if __name__ == "__main__":
    cleared = build(SOURCE_WORKBOOK, EXCEL_TEMPLATE)
    total = sum(cleared.values())
    print(f"Template written to {EXCEL_TEMPLATE.relative_to(Path.cwd())}")
    print(f"{total:,} data cells cleared; headers, merges, widths and styles kept.")
