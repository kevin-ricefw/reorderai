# Local data (not committed)

Keep dumps here for offline training / import. Git ignores contents except `.gitkeep`.

```text
data/
  sales/              Product Sales JAN|FEB|…|AUGUST *.csv
  inventory/          CURRENT INVENTORY COUNT.csv
  vendors/            vendor catalog / pack overrides
  Past Invoices/      invoice workbooks
  forecast_store/     nightly P50/P90 + SKU uplift (overwritten each night)
  cache/order_runs/   detect-order run_ids
```

Import into Paul tenant:

```bash
python scripts/import_local_to_paul.py --execute
```

Lead time and cover days come from the API request — not a schedule folder.
Timezone for festival “today”: `REORDER_TZ=America/Detroit`.
