# Reorder AI

Vendor reorder system for Wecomm POS (Indian grocery / Okemos).

## What this repo contains

| Area | Purpose |
|------|---------|
| `api/` | FastAPI — W-1 **detect-order**, vendor order, uploads, health |
| `app/dashboard/` | Streamlit analytics & vendor reorder UI |
| `v2/` | Demand forecasting, Syntetos–Boylan patterns, inventory math |
| `database/` | Wecomm Azure PostgreSQL connector |
| `docs/` | Live DB architecture audit for Reorder AI |
| `config/` | Settings (env + YAML) |
| `tests/` | Unit tests for core reorder / POS math |

## Setup

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env   # fill DB_* and optional OPENAI_API_KEY
```

### Database

Wecomm Postgres is reached through an SSH tunnel (Bastion → jump host):

`127.0.0.1:5433` → `wecomm.postgres.database.azure.com:5432`

Configure `.env` from `.env.example`. Keep the tunnel up before starting the API.

Architecture notes (schema-per-tenant): `docs/REORDER_AI_DATABASE_ARCHITECTURE.md`

## Run

**API**

```bash
python -m uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload
```

- UI: http://localhost:8000  
- Docs: http://localhost:8000/docs  
- DB check: `GET /api/db-health`  
- Detect order: `POST /api/detect-order`

**Streamlit dashboard**

```bash
streamlit run app/dashboard/main.py --server.port 8501
```

## Local data (not in git)

Place POS exports under `data/` locally (`sales/`, `inventory/`, `vendors/`, etc.). Those folders are gitignored.

## Tests

```bash
pytest -q
```
