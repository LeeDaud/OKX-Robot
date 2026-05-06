# Plan — Auto Trader 技术实施计划

## 技术选型

| 组件 | 选型 | 理由 |
|------|------|------|
| 链监听 | `web3.py` + Base WebSocket RPC | 原生支持 eth_subscribe |
| 交易解析 | ABI decode + `eth_getLogs` | 解析 Uniswap V2/V3 Swap 事件 |
| 交易执行 | OKX DEX Aggregator API | 覆盖多流动性来源，滑点保护 |
| 本地存储 | SQLite via `aiosqlite` | 轻量，无需额外服务 |
| 异步框架 | `asyncio` | WebSocket + HTTP 并发 |
| 配置管理 | `python-dotenv` + YAML | .env 存密钥，YAML 存业务配置 |

---

## Phase 1 — 核心跟单 MVP

**目标**：跑通完整链路，dry-run 验证后切 live。

**预计工期**：5-7 天

### 1.1 项目初始化

- [ ] 创建目录结构（见 CLAUDE.md）
- [ ] `requirements.txt`：web3, aiohttp, aiosqlite, python-dotenv, pyyaml
- [ ] `.env.example`：RPC_URL, PRIVATE_KEY, OKX_API_KEY, OKX_SECRET, OKX_PASSPHRASE
- [ ] `config/config.yaml`：copy_targets, trade_ratio, daily_loss_limit, slippage, dry_run
- [ ] SQLite schema 初始化脚本

### 1.2 链上监控模块 `src/monitor/`

```
monitor/
├── watcher.py      # WebSocket 订阅，监听目标地址的 pending tx
├── decoder.py      # 解析 swap 交易：识别 DEX router，提取 token_in/out/amount
└── filter.py       # 过滤非 swap 交易，过滤已处理 txhash
```

**关键实现点：**
- 订阅 `eth_subscribe("newPendingTransactions")` 或 `eth_subscribe("logs")`
- 识别常见 DEX router 地址（Uniswap V3, Aerodrome 等 Base 链主流 DEX）
- 解析 `Swap` 事件 log，提取 tokenIn/tokenOut/amountIn/amountOut
- 幂等检查：source_tx 已存在则跳过

### 1.3 交易执行模块 `src/executor/`

```
executor/
├── okx_client.py   # OKX DEX API 封装：get_quote, build_tx, broadcast
└── trader.py       # 组装跟单逻辑：计算金额 → 获取报价 → 签名 → 广播
```

**OKX DEX API 调用流程：**
1. `GET /api/v5/dex/aggregator/quote` — 获取报价
2. `GET /api/v5/dex/aggregator/swap` — 获取 calldata
3. 本地签名 → `eth_sendRawTransaction` 广播

**关键实现点：**
- 执行前检查 ETH 余额（gas）和 token 余额
- 设置 slippage（默认 1%）
- dry_run=True 时只打印报价，不签名不广播

### 1.4 风控模块 `src/risk/`

```
risk/
└── guard.py        # 每日亏损检查，执行前 gate
```

- 每日亏损 = 当日所有 status=success 的 pnl 之和（负值）
- 超过 `daily_loss_limit` 时 `guard.check()` 返回 False，trader 跳过执行

### 1.5 主入口 `src/main.py`

```python
# 启动流程
load_config → init_db → start_monitor → on_swap_detected → risk_check → execute
```

- `--dry-run` flag 覆盖 config 中的 dry_run 设置
- 优雅退出：捕获 SIGINT，等待当前交易完成后停止

### 1.6 测试

- `tests/test_decoder.py`：用真实 txhash 测试解析逻辑
- `tests/test_risk.py`：模拟亏损数据，验证 guard 逻辑
- `tests/test_okx_client.py`：mock OKX API，验证请求构造

---

## Phase 2 — 跟单金额自定义

**预计工期**：2-3 天

### 新增功能

- `config.yaml` 新增字段：
  ```yaml
  trade_mode: ratio        # ratio | fixed
  trade_ratio: 0.1         # ratio 模式：空闲余额的 10%
  trade_fixed_usd: 50      # fixed 模式：每笔 $50
  trade_max_usd: 200       # 单笔上限
  ```
- `trader.py` 中 `calculate_amount()` 根据 mode 分支计算
- 配置热更新：主循环每 60 秒重新加载 `config.yaml`

---

## Phase 3 — 多地址 & 过滤

**预计工期**：3-4 天

### 新增功能

- `config.yaml` 支持多个 `copy_targets`，每个地址独立配置 ratio/fixed
- `monitor/watcher.py` 并发订阅多个地址
- `filter.py` 新增：代币白名单过滤、最小交易额过滤

---

## Phase 4 — 通知 & 监控

**预计工期**：3-5 天

### 新增功能

- `python-telegram-bot` 集成
- 每笔跟单结果推送
- 每日 UTC 00:00 盈亏汇报
- `/pause` `/resume` `/status` 命令

---

## 关键风险

| 风险 | 缓解措施 |
|------|----------|
| WebSocket 断连 | 自动重连，指数退避，最多重试 10 次 |
| OKX API 限频 | 请求间隔 ≥ 200ms，429 时退避重试 |
| 被跟单地址 MEV/夹子 | 监控 pending tx 可能被抢跑，Phase 1 先用 confirmed tx |
| Gas 飙升导致亏损 | 执行前估算 gas，超过阈值跳过 |
| 私钥泄露 | .env 不进 git，.gitignore 强制排除 |

---

## 里程碑

| 阶段 | 交付物 | 验收标准 |
|------|--------|----------|
| Phase 1 | 可运行的 dry-run 机器人 | 检测到目标地址 swap → 打印跟单计划，延迟 < 3s |
| Phase 1 live | 切换 live 模式 | 成功执行 3 笔真实跟单，日志完整 |
| Phase 2 | 金额配置 | ratio/fixed 两种模式均可正常工作 |
| Phase 3 | 多地址 | 同时监控 3 个地址，互不干扰 |
| Phase 4 | Telegram 通知 | 跟单结果 30 秒内推送到 Telegram |
