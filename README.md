# OKX Copy Trading Robot

Base 链跟单机器人。监控指定钱包地址的链上 Swap 交易，通过 OKX DEX Aggregator API 自动跟单，支持止盈、风控、飞书通知。

## 功能

- 监控多个目标地址的链上 Swap（Uniswap V2/V3 协议）
- 按比例或固定金额跟单，支持单笔上限
- 每日亏损上限风控，触发后自动暂停
- 止盈监控：持仓收益率达到阈值自动卖出
- 飞书群机器人通知：跟单触发、止盈、整点汇报、每日汇报
- 整点汇报：钱包余额、浮动盈亏、实际盈亏、持仓列表
- 热更新：`config.yaml` 修改后 60 秒内自动生效，无需重启
- Dry-run 模式：只打印不发链上交易

## 快速开始

**1. 安装依赖**

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

**2. 配置环境变量**

```bash
cp .env.example .env
```

编辑 `.env`，填入：

| 字段 | 说明 |
|------|------|
| `RPC_HTTP_URL` | Base 链 HTTP RPC 地址 |
| `PRIVATE_KEY` | 执行钱包私钥（`0x` 开头） |
| `WALLET_ADDRESS` | 执行钱包地址 |
| `OKX_API_KEY` | OKX DEX API Key |
| `OKX_SECRET_KEY` | OKX DEX Secret Key |
| `OKX_PASSPHRASE` | OKX DEX Passphrase |

**3. 配置跟单参数**

编辑 `config.yaml`：

```yaml
copy_targets:
  - address: "0x目标钱包地址"

trade_mode: ratio        # ratio（按余额比例）或 fixed（固定金额）
trade_ratio: 0.50        # ratio 模式：使用 50% 的 USDC 余额
trade_fixed_usd: 50      # fixed 模式：每笔 50 USDC
trade_max_usd: 100       # 单笔上限

daily_loss_limit_usd: 10 # 每日最大亏损
take_profit_roi: 0.30    # 止盈阈值（30%），0 = 不启用

dry_run: true            # 先用 dry-run 测试
```

**4. 运行**

```bash
# Dry-run 模式（默认，不发链上交易）
python src/main.py

# 强制 dry-run
python src/main.py --dry-run

# 实盘模式
python src/main.py --live
```

## 目录结构

```
src/
├── config/       # 配置加载（.env + config.yaml）
├── db/           # SQLite 持久化（交易记录、持仓）
├── executor/     # OKX DEX 客户端、交易执行
├── monitor/      # 链上地址监控、Swap 解码、过滤
├── notify/       # 飞书通知
└── risk/         # 风控（每日亏损上限、止盈监控）
```

## 部署（VPS）

```bash
# 首次部署
rsync -av --exclude='.env' --exclude='.venv' --exclude='logs' --exclude='*.db' \
  ./ root@<server>:/opt/okx-robot/
scp .env root@<server>:/opt/okx-robot/.env
ssh root@<server> "bash /opt/okx-robot/deploy/install.sh"

# 后续更新
ssh root@<server> "bash /opt/okx-robot/deploy/update.sh"

# 查看日志
ssh root@<server> "journalctl -u okx-robot -f"
```

## 测试

```bash
python -m pytest tests/ -x
```
