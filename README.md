# Auto Trader

Base 链跟单机器人。监控目标钱包地址的链上交易，通过 OKX DEX Aggregator API 自动跟单，支持止盈卖出、风控止损、飞书通知。

## 功能

- **链上监控** — 通过 `eth_getLogs` 轮询目标地址的 Transfer 事件，支持 Uniswap V2/V3 / Aerodrome / Virtuals 等 DEX
- **自动跟单** — 买入时用 USDC 支付跟随买入，卖出时将持仓代币换回 USDC
- **止盈监控** — 定时轮询持仓代币价格，收益率达到阈值自动卖出
- **回购检测** — 监控指定回购地址的买入行为，立即卖出对应持仓
- **风控** — 每日亏损上限触发后自动暂停；Gas price 过高时跳过；报价蜜罐/价格影响/税率校验
- **崩溃恢复** — 交易发出后立即持久化到 SQLite，重启后自动确认并回填成交
- **飞书通知** — 跟单触发、成交回填、止盈卖出、整点汇报、风控警报
- **热更新** — `config.yaml` 修改后 60 秒内自动生效，无需重启
- **Dry-run** — 只打印不发链上交易

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

trade_mode: ratio            # ratio（按余额比例）或 fixed（固定金额）
trade_ratio: 0.50            # ratio 模式：使用 50% 的 USDC 余额
trade_fixed_usd: 50          # fixed 模式：每笔 50 USDC
trade_max_usd: 100           # 单笔上限
slippage: 0.10               # 最大滑点 10%

daily_loss_limit_usd: 10     # 每日最大亏损
take_profit_roi: 0.30        # 止盈阈值（30%），0 = 不启用

dry_run: true                # 先用 dry-run 测试
```

**4. 运行**

```bash
# Dry-run 模式（默认，不发链上交易）
python -m src.main

# 实盘模式
python -m src.main --live
```

## 目录结构

```
src/
├── config/       # 配置加载（.env + config.yaml）
├── db/           # SQLite 持久化（交易记录、持仓）
├── executor/     # OKX DEX 客户端、交易执行（approve + swap）
├── monitor/      # 链上地址监控、Swap 解码、Tx 过滤
│   ├── watcher.py   # AddressWatcher：eth_getLogs 轮询
│   ├── decoder.py   # Swap 事件解析（V2/V3/Transfer 兜底）
│   └── filter.py    # 代币白名单、最小交易额过滤
├── notify/       # 飞书群机器人通知
├── risk/         # 风控（每日亏损上限、止盈监控）
└── rpc/          # RPC 路由（主 RPC + 备用 fallback）
```

## 交易流程

```
监控检测 → 解码 Swap → 风控过滤 → 计算跟单金额
  → OKX 报价 → 报价风控校验（蜜罐/价格影响/税率）
  → 检查 Allowance → Approve（如需） → 签名广播
  → 持久化 tx_hash → 等待确认 → 回填成交 → 飞书通知
```

## 部署（VPS）

```bash
# ssh 登录后更新
ssh root@<server>
cd /opt/auto-trader
git pull --ff-only origin master
systemctl restart auto-trader

# 查看日志
journalctl -u auto-trader -f
```

首次部署参考 `deploy/install.sh`。

## 测试

```bash
python -m pytest tests/ -x
```
