#!/usr/bin/env bash
# 更新脚本（已部署后使用）
set -euo pipefail

DEPLOY_DIR=/opt/okx-robot

cd "$DEPLOY_DIR"
echo "=== 拉取最新代码 ==="
git pull --ff-only origin master

echo "=== 安装依赖 ==="
.venv/bin/pip install -r requirements.txt -q

echo "=== 重启服务 ==="
systemctl restart okx-robot
sleep 2
systemctl status okx-robot --no-pager
