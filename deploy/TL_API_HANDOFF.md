# Reorder AI — API handoff for TL

## Base URL

`http://74.249.36.238:8000`

| Link | Purpose |
|------|---------|
| `/docs` | Swagger UI |
| `/api/health` | Liveness |
| `/api/db-health` | Postgres connectivity |
| `GET /api/detect-order` | List vendors |
| `POST /api/detect-order` | Main reorder API |
| `GET /api/detect-order/runs/{run_id}/export.xlsx` | Excel order sheet |

## Example request

```http
POST /api/detect-order
Content-Type: application/json

{
  "vendor_id": "18",
  "lead_time_days": 3,
  "time_to_cover_days": 14,
  "include_zero_orders": false
}
```

## What the API returns (summary)

- **Action:** ORDER / WATCH / SKIP  
- **Qty:** full cases only, sized for cover **C** after lead **L**  
- **ADS:** from live ~90-day sales (not invented from ML)  
- **P50/P90:** reference only  
- **Justification:** plain report from the numbers  
- **Festivals:** tags in next X = L+C days (Michigan timezone)

## Ops notes

- Host: Azure VM `/opt/reorder-ai`, systemd service `reorder-ai`
- Nightly forecast: `reorder-nightly-forecast.timer` at **02:00 America/Detroit**
- Overwrites `data/forecast_store/` each night
- API has **no auth** yet — share only inside the team

Full workflow: [`docs/COMPLETE_SYSTEM_WORKFLOW.md`](../docs/COMPLETE_SYSTEM_WORKFLOW.md)
