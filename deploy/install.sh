#!/usr/bin/env bash
set -euo pipefail

DEPLOY_DIR=/opt/okx-robot
SERVICE_NAME=okx-robot
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
PYTHON_BIN="${DEPLOY_DIR}/.venv/bin/python"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Please run as root."
  exit 1
fi

cd "$DEPLOY_DIR"

if [[ ! -f requirements.txt ]]; then
  echo "requirements.txt not found in ${DEPLOY_DIR}"
  exit 1
fi

if [[ ! -f config.yaml ]]; then
  echo "config.yaml not found in ${DEPLOY_DIR}"
  exit 1
fi

if [[ ! -f .env ]]; then
  echo ".env not found in ${DEPLOY_DIR}"
  exit 1
fi

echo "=== [1/6] Prepare runtime directories ==="
mkdir -p "${DEPLOY_DIR}/logs"

echo "=== [2/6] Create or refresh virtualenv ==="
if [[ ! -x "$PYTHON_BIN" ]]; then
  python3 -m venv "${DEPLOY_DIR}/.venv"
fi
"$PYTHON_BIN" -m pip install --upgrade pip -q
"$PYTHON_BIN" -m pip install -r requirements.txt -q

echo "=== [3/6] Validate local config ==="
"$PYTHON_BIN" -m src.main --check-config

echo "=== [4/6] Install systemd service ==="
install -m 0644 deploy/okx-robot.service "$SERVICE_FILE"
systemctl daemon-reload
systemctl enable "$SERVICE_NAME" >/dev/null

echo "=== [5/6] Restart service ==="
systemctl restart "$SERVICE_NAME"
sleep 3

echo "=== [6/6] Verify service status ==="
systemctl status "$SERVICE_NAME" --no-pager
echo
echo "Logs: journalctl -u ${SERVICE_NAME} -f"
