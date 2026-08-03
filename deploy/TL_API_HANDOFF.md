# Reorder AI — API handoff for TL

## Base URL

After deploy, use the printed URL (example):

`https://<app-name>.azurewebsites.net`

| Link | Purpose |
|------|---------|
| `/docs` | Swagger UI (try endpoints) |
| `/api/health` | Liveness |
| `/api/db-health` | Postgres connectivity |
| `GET /api/detect-order` | List vendors |
| `POST /api/detect-order` | Main reorder API |

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

## Notes

- Nightly ML batch is **not** run inside Azure yet — `data/forecast_store/` is packaged at deploy time.
- Live stock/vendors need Azure Postgres firewall to allow the App Service.
- API has **no auth** yet — share URL only inside the team, or add API key / Easy Auth later.
