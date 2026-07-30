"""Central paths for raw store data (sales, inventory, vendors, delivery schedule)."""

from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

# All POS / vendor inputs live under data/
DATA_ROOT = PROJECT_ROOT / "data"

SALES_DIR = DATA_ROOT / "sales"
INVENTORY_PATH = DATA_ROOT / "inventory" / "current inventory count.csv"
VENDORS_DIR = DATA_ROOT / "vendors"
PACK_OVERRIDES_PATH = VENDORS_DIR / "pack_overrides.csv"
SCHEDULE_PATH = DATA_ROOT / "delivery_timings" / "Vendor_Order_Schedule_Updated.xlsx"
WEATHER_CACHE_DIR = DATA_ROOT / "cache"
WASTE_DIR = DATA_ROOT / "waste"
SANDBOX_EXPORTS_DIR = DATA_ROOT / "sandbox_exports"
