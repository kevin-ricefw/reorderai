#!/usr/bin/env bash
# Install nightly forecast systemd timer on the Azure VM.
# Usage (on VM): sudo bash deploy/install-nightly-forecast.sh
set -euo pipefail

ROOT=/opt/reorder-ai
UNIT_DIR=/etc/systemd/system

if [[ ! -d "$ROOT" ]]; then
  echo "ERROR: $ROOT not found"
  exit 1
fi

mkdir -p /var/log/reorder-ai
chown azureuser:azureuser /var/log/reorder-ai

cp "$ROOT/deploy/reorder-nightly-forecast.service" "$UNIT_DIR/"
cp "$ROOT/deploy/reorder-nightly-forecast.timer" "$UNIT_DIR/"

# Ensure VM uses live DB for nightly batch
ENV_FILE="$ROOT/.env"
touch "$ENV_FILE"
set_kv() {
  local key="$1" val="$2"
  if grep -q "^${key}=" "$ENV_FILE"; then
    sed -i "s|^${key}=.*|${key}=${val}|" "$ENV_FILE"
  else
    echo "${key}=${val}" >> "$ENV_FILE"
  fi
}
set_kv FORECAST_USE_LOCAL_SALES 0
set_kv FORECAST_STORE_USE_BATCH 1
set_kv FORECAST_STORE_USE_LIVE_SQL 1
set_kv SKU_UPLIFT_ENABLED 1
set_kv REORDER_TZ America/Detroit

systemctl daemon-reload
systemctl enable --now reorder-nightly-forecast.timer
systemctl status reorder-nightly-forecast.timer --no-pager || true
systemctl list-timers reorder-nightly-forecast.timer --no-pager || true
echo "Installed. Logs: /var/log/reorder-ai/nightly-forecast.log"
echo "Manual test: sudo systemctl start reorder-nightly-forecast.service"
