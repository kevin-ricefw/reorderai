# Local data (not committed)

Keep dumps here for offline training / import. Git ignores all contents except `.gitkeep`.

```text
data/
  sales/              Product Sales JAN|FEB|... *.csv
  inventory/          current inventory count.csv
  vendors/            vendor catalog .xlsx
  Past Invoices/      invoice workbooks / PDFs
  GIFTCARD/           gift card export
  forecast_store/     nightly P50/P90 output
  cache/order_runs/   detect-order run_ids
```

Lead time and cover days come from the API request — not a schedule folder.
