#!/usr/bin/env bash
# 服务器端一键部署脚本（首次部署）
set -euo pipefail

DEPLOY_DIR=/opt/okx-robot

echo "=== [1/5] 创建目录 ==="
mkdir -p "$DEPLOY_DIR/logs"

echo "=== [2/5] 安装 Python 依赖 ==="
cd "$DEPLOY_DIR"
python3 -m venv .venv
.venv/bin/pip install --upgrade pip -q
.venv/bin/pip install -r requirements.txt -q

echo "=== [3/5] 安装 systemd 服务 ==="
cp deploy/okx-robot.service /etc/systemd/system/okx-robot.service
systemctl daemon-reload
systemctl enable okx-robot

echo "=== [4/5] 启动服务 ==="
systemctl restart okx-robot
sleep 3

echo "=== [5/5] 验证 ==="
systemctl status okx-robot --no-pager
echo ""
echo "日志: journalctl -u okx-robot -f"
