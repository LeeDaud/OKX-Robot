#!/usr/bin/env bash
set -euo pipefail

DEPLOY_DIR=/opt/auto-trader
SERVICE_NAME=auto-trader
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
PYTHON_BIN="${DEPLOY_DIR}/.venv/bin/python"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Please run as root."
  exit 1
fi

cd "$DEPLOY_DIR"

if [[ ! -d .git ]]; then
  echo "${DEPLOY_DIR} is not a git repository"
  exit 1
fi

if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "Virtualenv not found at ${PYTHON_BIN}"
  exit 1
fi

echo "=== [1/6] Pull latest code ==="
git pull --ff-only origin master

echo "=== [2/6] Install dependencies ==="
"$PYTHON_BIN" -m pip install -r requirements.txt -q

echo "=== [3/6] Refresh systemd unit ==="
install -m 0644 deploy/auto-trader.service "$SERVICE_FILE"
systemctl daemon-reload

echo "=== [4/6] Validate config ==="
"$PYTHON_BIN" -m src.main --check-config

echo "=== [5/6] Run tests ==="
"$PYTHON_BIN" -m pytest tests/ -x -q

echo "=== [6/6] Restart service ==="
systemctl restart "$SERVICE_NAME"
sleep 2
systemctl status "$SERVICE_NAME" --no-pager
