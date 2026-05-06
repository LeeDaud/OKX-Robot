# Auto Trader

Base 链自动化套利机器人。通过 OKX DEX Aggregator API 执行确定性交易策略：**定时定投积累持仓 → 监控回购事件套利卖出 → 止盈自动退出**，实现稳定的链上套利收益。

## 策略

| 策略 | 说明 |
|------|------|
| **定时定投 (DCA)** | 每日指定时间窗口用 USDC 买入目标代币。自动积累持仓 |
| **回购套利 (Buyback)** | 监控回购地址的链上买入事件，检测到回购立即卖出对应持仓，吃回购拉涨的价差 |
| **止盈 (Take Profit)** | 定时轮询持仓价格，ROI 达到阈值时自动卖出，锁定利润 |

三种策略形成完整链路：**买入 → 持有 → 卖出**。

## 功能

- **DCA 定投** — 支持多代币、可配置时间窗口，每日不重复
- **回购检测** — 通过 `eth_getLogs` 监控指定地址的 ERC-20 Transfer 转入事件
- **止盈监控** — 定时轮询 OKX 报价，ROI 达标自动卖出
- **风控** — 每日亏损上限自动暂停；Gas price 过高时跳过；报价安全校验（蜜罐/价格影响/税率）
- **崩溃恢复** — 交易发出后立即持久化到 SQLite，重启后自动确认并回填成交
- **进程锁** — 防止重复启动
- **飞书通知** — 买入触发、卖出成交、止盈、整点汇报、风控警报
- **状态持久化** — 原子文件写入，崩溃不丢状态
- **RPC 主备切换** — 主 RPC 超时自动切换到备用 RPC，指数退避防限频
- **Nonce 重放保护** — 交易广播后验证 nonce 是否消耗，未消耗则重发

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

编辑 `.env`，填入 RPC 地址、钱包私钥、OKX DEX API 凭证：

| 字段 | 说明 |
|------|------|
| `RPC_HTTP_URL` | Base 链 HTTP RPC 地址 |
| `RPC_HTTP_URL_FALLBACK` | 备用 RPC（可选） |
| `PRIVATE_KEY` | 执行钱包私钥（`0x` 开头） |
| `WALLET_ADDRESS` | 执行钱包地址 |
| `OKX_API_KEY` | OKX DEX API Key |
| `OKX_SECRET_KEY` | OKX DEX Secret Key |
| `OKX_PASSPHRASE` | OKX DEX Passphrase |

**3. 配置策略参数**

编辑 `config.yaml`：

```yaml
# 定时定投
dca:
  enabled: true
  tokens:
    - address: "0x目标代币地址"
      amount_usdc: 2           # 每次定投 2 USDC
      hour: 24                 # 北京时间 00:00（24 = 次日 0 点）
      minute: 0
      window_minutes: 30       # 窗口持续 30 分钟

# 回购套利
buyback_watch:
  "0x回购地址": "0x目标代币地址"

# 止盈
take_profit_roi: 0.30          # 30% 止盈

# 运行
dry_run: true                  # 先用 dry-run 测试
```

**4. 运行**

```bash
# 配置校验（不启动）
PYTHONPATH=. python src/main.py --check-config

# Dry-run 模式（默认，不发链上交易）
PYTHONPATH=. python src/main.py --dry-run

# 实盘模式
PYTHONPATH=. python src/main.py --live
```

## 目录结构

```
src/
├── config/       # 配置加载（.env + config.yaml）
├── db/           # SQLite 持久化（交易记录、持仓）
├── executor/     # OKX DEX 客户端、交易执行（approve + swap）
├── monitor/      # 链上监控
│   └── buyback.py   # 回购事件监控（ERC-20 Transfer → 回购地址）
├── notify/       # 飞书群机器人通知
├── risk/         # 风控（每日亏损上限、止盈监控）
├── rpc/          # RPC 路由（主 RPC + 备用 fallback）
└── state/        # 状态持久化 + 进程锁
```

## 交易流程

```
                    ┌──────────────────┐
                    │   DCA 定时器      │
                    │  (每分钟检查)      │
                    └───────┬──────────┘
                            │ 到达时间窗口
                            ▼
                    ┌──────────────────┐
                    │  OKX 报价 → 校验  │
                    │  Approve → Swap  │
                    │  回填成交 → 通知  │
                    └───────┬──────────┘
                            │ 持仓增加
                            ▼
              ┌─────────────┴─────────────┐
              │                           │
              ▼                           ▼
    ┌──────────────────┐       ┌──────────────────┐
    │  BuybackMonitor  │       │  TakeProfitMonitor│
    │  (监听回购事件)    │       │  (定时扫描持仓)    │
    └───────┬──────────┘       └───────┬──────────┘
            │ 检测到回购                │ ROI 达标
            ▼                           ▼
    ┌──────────────────┐       ┌──────────────────┐
    │  卖出持仓 → 套利  │       │  卖出持仓 → 止盈  │
    │  记录盈亏 → 通知  │       │  记录盈亏 → 通知  │
    └──────────────────┘       └──────────────────┘
```

## 风控

- **每日亏损上限**：当日已实现亏损超过 `daily_loss_limit_usd` 时暂停所有交易
- **Gas 保护**：Gas price 超过 `gas_limit_gwei` 时跳过
- **报价安全**：自动检测蜜罐代币、价格影响 > 5%、税率 > 5% 时跳过
- **进程锁**：同一时间只允许一个实例运行

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
PYTHONPATH=. python -m pytest tests/ -x
```

## 与参考项目的关系

本项目借鉴了以下项目的核心模式：

- **Auto-Buyer** — DCA 定投策略、状态持久化、原子文件写入
- **Auto-Seller** — 回购套利检测、交易缓存预加载、多 RPC 广播
