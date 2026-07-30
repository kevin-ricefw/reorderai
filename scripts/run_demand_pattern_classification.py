"""
Syntetos-Boylan demand-pattern classification for all POS items.

Computes ADI + CV² per UPC from sales history and writes an Excel report.

Run:
  python scripts/run_demand_pattern_classification.py
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pandas as pd

from app.dashboard.pos_data_service import load_inventory, load_sales_detailed
from scripts.run_sku_analysis import resolve_analysis_window
from v2.analytics.syntetos_boylan import (
    CLASS_ERRATIC,
    CLASS_INTERMITTENT,
    CLASS_LUMPY,
    CLASS_NO_DEMAND,
    CLASS_SINGLE_HIT,
    CLASS_SMOOTH,
    classify_skus_syntetos_boylan,
    methodology_dataframe,
    summarize_demand_classes,
)

OUTPUT_DIR = ROOT / "outputs" / "analytics"


def _sheet_name(name: str) -> str:
    # Excel sheet names max 31 chars
    return name[:31]


def write_excel_report(classified: pd.DataFrame, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    summary = summarize_demand_classes(classified)
    method = methodology_dataframe()

    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        classified.to_excel(writer, sheet_name="All_Items", index=False)
        summary.to_excel(writer, sheet_name="Summary", index=False)
        method.to_excel(writer, sheet_name="Methodology", index=False)

        for cls, sheet in [
            (CLASS_SMOOTH, "Smooth"),
            (CLASS_INTERMITTENT, "Intermittent"),
            (CLASS_ERRATIC, "Erratic"),
            (CLASS_LUMPY, "Lumpy"),
            (CLASS_SINGLE_HIT, "Single_Demand_Day"),
            (CLASS_NO_DEMAND, "No_Demand"),
        ]:
            part = classified[classified["demand_class"] == cls]
            part.to_excel(writer, sheet_name=_sheet_name(sheet), index=False)

    return path


def run(
    *,
    start_date: date | None = None,
    end_date: date | None = None,
    output_dir: Path | None = None,
) -> Path:
    analysis_start, analysis_end = resolve_analysis_window(start_date, end_date)
    out_dir = output_dir or OUTPUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"Loading sales {analysis_start} to {analysis_end}...")
    sales = load_sales_detailed(start_date=analysis_start, end_date=analysis_end)
    inventory = load_inventory()

    if sales.empty:
        raise ValueError(
            f"No sales rows between {analysis_start} and {analysis_end}. "
            "Upload Product Sales CSVs first."
        )

    print(f"  Sales rows: {len(sales):,}  |  Unique UPCs: {sales['upc'].nunique():,}")
    print("Computing Syntetos-Boylan ADI / CV2 for all items...")

    classified = classify_skus_syntetos_boylan(
        sales,
        inventory=inventory,
        start_date=analysis_start,
        end_date=analysis_end,
    )
    summary = summarize_demand_classes(classified)
    print("\nClass counts:")
    for _, row in summary.iterrows():
        print(
            f"  {row['demand_class']:<20}  SKUs={int(row['sku_count']):>6}  "
            f"({row['sku_pct']:5.1f}%)  qty_share={row['qty_pct']:5.1f}%"
        )

    stamp = f"{analysis_start.isoformat()}_to_{analysis_end.isoformat()}"
    xlsx_path = out_dir / f"syntetos_boylan_demand_patterns_{stamp}.xlsx"
    # Also write a stable "latest" name for easy finding
    latest_path = out_dir / "syntetos_boylan_demand_patterns.xlsx"

    write_excel_report(classified, xlsx_path)
    write_excel_report(classified, latest_path)

    # CSV companion for quick filters
    csv_path = out_dir / "syntetos_boylan_demand_patterns.csv"
    classified.to_csv(csv_path, index=False)

    print(f"\nExcel report: {xlsx_path}")
    print(f"Latest copy:  {latest_path}")
    print(f"CSV:          {csv_path}")
    return latest_path


if __name__ == "__main__":
    run()
