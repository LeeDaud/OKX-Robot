# 合约交易接入方案

## 1. 背景

当前交易链路基于 OKX DEX Aggregator API (v6)，仅支持 Base 链上现货 DEX 兑换。如需进行永续合约交易，需接入 OKX CEX API (v5)。

## 2. 整体架构

```
现有链路（保持不变）                    新增合约链路
┌──────────────┐                  ┌──────────────┐
│ 策略模块      │                  │ 策略模块      │
│ (DCA/Grid/)  │                  │ (合约专用)    │
└──────┬───────┘                  └──────┬───────┘
       │                                  │
       ▼                                  ▼
┌──────────────┐                  ┌──────────────┐
│ Trader       │                  │ ContractTrader│
│ (现货执行器)  │                  │ (合约执行器)   │
└──────┬───────┘                  └──────┬───────┘
       │                                  │
       ▼                                  ▼
┌──────────────┐                  ┌──────────────┐
│ OKXDexClient │                  │ OKXCexClient  │
│ (v6 DEX API) │                  │ (v5 CEX API)  │
└──────────────┘                  └──────┬───────┘
       │                                  │
       ▼                                  ▼
  Base链 DEX                         OKX 中心化交易所
  (Uniswap/Aerodrome)                (永续合约撮合)
```

**关键原则**：现货链路完全不改动，合约作为独立链路接入。

## 3. OKX CEX API v5 核心接口

### 3.1 价格与市场数据

| 接口 | 说明 |
|------|------|
| `GET /api/v5/market/ticker?instId={pair}` | 实时 ticker 价格 |
| `GET /api/v5/market/index-tickers?instId={pair}` | 指数价格 |
| `GET /api/v5/market/candles?instId={pair}&bar={granularity}` | K 线数据 |
| `GET /api/v5/public/instruments?instType=SWAP` | 所有永续合约交易对信息 |

### 3.2 合约交易

| 接口 | 说明 |
|------|------|
| `POST /api/v5/trade/order` | 下单（开/平） |
| `POST /api/v5/trade/close-position` | 市价全平 |
| `GET /api/v5/trade/orders-pending` | 当前挂单 |
| `GET /api/v5/trade/fills` | 成交明细 |
| `POST /api/v5/trade/cancel-order` | 撤单 |
| `POST /api/v5/trade/order-algo` | 止损/止盈/追踪止损 |

### 3.3 账户与风控

| 接口 | 说明 |
|------|------|
| `GET /api/v5/account/balance` | 账户资产余额 |
| `GET /api/v5/account/positions` | 持仓详情 |
| `POST /api/v5/account/set-leverage` | 设置杠杆 |
| `GET /api/v5/account/risk-state` | 账户风控状态 |
| `POST /api/v5/account/position-risk` | 手动减仓 |

### 3.4 签名

OKX v5 签名与 v6 完全一致（HMAC-SHA256 + Base64），现有 `_sign()` 函数可直接复用。

## 4. 新增模块

### 4.1 `src/executor/okx_cex_client.py`

OKX CEX v5 API 封装，可选两种实现方式：

**方式 A：官方 SDK**（推荐）
```python
from okx import Market, Trade, Account

market_api = Market.MarketAPI(
    api_key, secret_key, passphrase, flag='0'  # 0=实盘
)
ticker = market_api.get_ticker(instId="BTC-USDT-SWAP")
```

```bash
pip install okx
```

**方式 B：自实现**（轻量，复用现有签名）
仅封装合约交易用到的小接口子集，不引入全量 SDK。

### 4.2 `src/executor/contract_trader.py`

合约交易执行器，职责：

- `open_long(pair, size_usd, leverage)` → 开多
- `open_short(pair, size_usd, leverage)` → 开空
- `close_position(pair, direction)` → 平仓
- `set_stop_loss(pair, price)` → 设置止损
- `set_take_profit(pair, price)` → 设置止盈
- `adjust_margin(pair, amount)` → 调整保证金

### 4.3 `src/risk/contract_risk.py`

合约风控模块：

- 保证金率实时监控（低于阈值触发减仓/强平预警）
- 资金费率监控与成本计算
- 杠杆倍数约束（按品种限制最大杠杆）
- 总体风险敞口限制

## 5. 合约专用策略

### 5.1 趋势跟踪

```
signal = 价格突破MA(20) && 成交量放大且资金费率为正
  → 开多，浮动止损
```

### 5.2 网格增强

现有现货网格的合约版本：在窄幅区间内双向挂单，同时赚取资金费率。

```
Grid level:  价格  → 开多   → TP
             价格 ↓ → 开空   → TP
             震荡区间内同时吃双向收益 + 资金费率
```

### 5.3 对冲套利

```
现货持仓 → 合约做空等量 → 锁定价差
基差走阔时平仓 → 吃交割溢价
```

## 6. 风控设计

合约交易相比现货的关键额外风险：

| 风险 | 措施 |
|------|------|
| 强平 | 维持保证金率 > 2x 强平线，实时轮询监控 |
| 资金费率 | 费率 > 0.1% 时自动移仓或平仓 |
| 滑点 | 限价单为主，市价单只用于紧急平仓 |
| 单品种敞口 | 总保证金不超过账户净值 30% |
| 杠杆约束 | 主流币 ≤ 5x，山寨 ≤ 3x |
| 熔断 | 单日亏损达账户净值 10% 时停止所有合约交易 |

## 7. 资金流

```
链上钱包 (Base)
    │
    │ OKX 充值通道（网络：Base → OKX）
    ▼
OKX CEX 资金账户
    │
    │ 内部划转
    ▼
OKX CEX 交易账户（逐仓/全仓）
    │
    │ 下单
    ▼
永续合约仓位
```

## 8. 开发阶段

### 阶段一：基础设施

1. 实现 `okx_cex_client.py`（价格查询 + 账户查询）
2. 实现 `contract_trader.py`（下单 + 平仓）
3. `.env` 补充 OKX CEX 相关配置
4. dry-run 模式验证下单流程

### 阶段二：风控

1. 实现 `contract_risk.py`
2. 持仓状态持久化（复用 `src/db/`）
3. 强平监测守护协程
4. 飞书通知（开仓/平仓/强平预警/资金费率）

### 阶段三：策略

1. 选择一种合约策略实现
2. 回测或小资金验证
3. 上线运行
