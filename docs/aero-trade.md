# PRD：Base 链 AERO 趋势交易机器人 MVP

> 本文档在源 PRD 基础上重构，要点是：**尽可能复用现有代码**，不引入新框架、新 API 通道或额外数据源。

---

## 1. 项目背景

当前交易机器人已具备自动交易能力，但核心难点在于交易策略。本阶段不做套利、搬砖、三角套利，不做多代币扫描与复杂信号评分。

**目标**：聚焦 AERO 单标的趋势交易，让机器人在满足明确趋势条件时买入，在趋势失效、触发止损、达到止盈时卖出。后续可扩展到 VIRTUAL、BRETT、TOSHI、DEGEN 及 Virtuals 新发射代币。

---

## 2. 代码复用全景图

现有模块按复用方式分为三类。**标绿 = 零改动直接复用**，**标黄 = 扩展配置即可**，**标红 = 需新建模块**。

```
现有模块复用评估：
┌──────────────────────────────────────────────────────┐
│  ✅ 零改动复用                                        │
│    src/rpc/router.py        → 链上 RPC 访问          │
│    src/executor/okx_client.py → AERO/USDC 报价+换汇   │
│    src/executor/trader.py    → 买入/卖出执行            │
│    src/risk/guard.py         → 每日亏损上限            │
│    src/state/persistence.py  → 状态持久化              │
│    src/notify/feishu.py      → 飞书通知                │
│    src/db/database.py        → 交易记录+持仓查询       │
│    src/web/api.py            → REST API（扩展路由）    │
├──────────────────────────────────────────────────────┤
│  📝 扩展配置（加一个 dataclass + YAML 段即可）          │
│    src/config/loader.py      → 加 AeroTrendConfig     │
│    config.yaml               → 加 aero_trend: 段      │
│    src/main.py               → 加 aero_loop 协程      │
├──────────────────────────────────────────────────────┤
│  🆕 新建模块（3 个文件）                               │
│    src/strategy/aero_collector.py                     │
│    src/strategy/aero_position.py                      │
│    src/strategy/aero_strategy.py                      │
└──────────────────────────────────────────────────────┘
```

### 复用细节

| 现有模块 | 如何复用 | 改动量 |
|---------|---------|--------|
| `executor/trader.py` | `trader.buy(token, amount, payment_token=USDC_BASE)` 直接买入 AERO；`trader.sell(token)` 直接卖出。OKX DEX API 已支持 AERO/USDC 对。 | 0 行 |
| `executor/okx_client.py` | `okx.get_quote(AERO, USDC, amount)` 获取实时报价和滑点。已有 get_quote + build_swap_tx。 | 0 行 |
| `rpc/router.py` | Base 链 RPC 自动主备切换，用于链上数据采集（池子状态、swap 事件）。 | 0 行 |
| `risk/guard.py` | `guard.can_trade()` 直接用于单日亏损上限检查。 | 0 行 |
| `db/database.py` | `strategy='aero_trend'` 写入 trade 表；`get_open_positions()` 查 AERO 持仓；`get_today_stats()` 用于风控。 | 0 行 |
| `state/persistence.py` | `StateManager` 持久化持仓状态和冷却期。 | 0 行 |
| `monitor/buyback.py` | 其 `eth_getLogs` 轮询模式 + `fromBlock` 追踪逻辑可直接复用为 Aerodrome 池的 swap 事件采集。 | 模式复用 |
| `config/loader.py` | 仿照 `GridConfig` 加 `AeroTrendConfig(entry_xxx, exit_xxx)` | ~30 行 |
| `main.py` | 仿照 `grid_loop/sniper_loop` 加 `aero_loop` | ~50 行 |

---

## 3. 产品定位

只在 Base 链交易 AERO 的现货趋势交易机器人。不使用杠杆，不做合约，不做空。

**策略核心**：
1. 价格动量 — AERO 是否在短周期出现上涨趋势
2. 成交量放大 — 当前成交量是否显著高于平均水平
3. 流动性质量 — 池子深度是否足以支持无滑点或低滑点交易

**策略周期**：1m / 5m / 15m / 30m / 1h。
**交易对**：AERO / USDC（Base）。

---

## 4. 非目标范围

同源 PRD，补充一条：**重新造框架。现有 Trader + OKXDexClient + RPCRouter + DailyLossGuard 完全不需要替换。**

---

## 5. 核心策略框架

### 5.1 买入信号

| 类型 | 条件数 | 说明 |
|------|-------|------|
| 趋势启动型 | 11 个条件全部满足时买入 | 主要策略 |
| 强势回踩型 | 11 个条件全部满足时买入 | 次优策略 |
| 新币早期确认型 | 本阶段不启用 | 保留扩展 |

### 5.2 卖出信号

| 类型 | 优先级 | 触发条件 |
|------|--------|---------|
| 硬止损 | P0 | 浮亏 ≥ 7% → 全部卖出 |
| 信号反转 | P1 | 卖压 > 65% 或 跌破 VWAP 等（任意 2/5）→ 全部卖出 |
| 时间止损 | P2 | 持仓 > 60min 且浮盈 < 3% → 全部卖出 |
| 分批止盈 | P3 | 10% 卖 30% → 20% 再卖 30% → 剩余 40% 移动止盈 |
| 移动止盈 | P4 | 从最高点回撤 ≥ 8% → 全部卖出 |

**多规则冲突时执行优先级最高的规则**（P0 > P1 > P2 > P3 > P4）。

### 5.3 仓位规则

| 规则 | 值 |
|------|----|
| 初始仓位 | 总资金 5% |
| 连续 3 笔亏损后 | 降至 2.5% |
| 连续 5 笔亏损后 | 当日停止交易 |
| 单日亏损 ≥ 3% | 停止交易 |
| 单日盈利 ≥ 8% | 仓位减半（保守模式） |
| 冷却期 | 止损后 30min 内不重新买入 |
| 加仓限制 | 不允许加仓、不允许摊平 |
| 持仓限制 | 最多 1 笔 AERO 持仓 |
| 重试限制 | 买入最多重试 1 次，卖出最多重试 3 次 |

### 5.4 仓位管理对比

原 PRD 定义的仓位规则（连续亏损降仓、冷却期、单日限制等）**全部已在 `DailyLossGuard` 和 `StrategyState` 的能力范围内**。

- `DailyLossGuard` 已实现单日亏损上限、日 PnL 累计和日期自动重置
- `StrategyState.is_on_cooldown()` 已实现冷却期检查
- 需要新增的是：**连续交易盈亏统计**（记录最近 N 笔交易的 PnL），这个可以复用 `get_trades_by_strategy('aero_trend')` 并截取最近 N 条计算

---

## 6. 指标定义

### 6.1 价格动量

全部通过 **OKX `get_quote` 和链上池子数据**计算，不需要外部行情 API。

| 指标 | 计算方式 | 数据来源 |
|------|---------|---------|
| 当前价格 | AERO/USDC OKX quote | `okx.get_quote(AERO, USDC, 0.1e6)` |
| 5m 涨幅 | `(price - price_5m_ago) / price_5m_ago` | 内部滚动窗口 |
| 15m 涨幅 | 同上 | 内部滚动窗口 |
| 30m 涨幅 | 同上 | 内部滚动窗口 |
| 1h 涨幅 | 同上 | 内部滚动窗口 |
| VWAP | 量加权平均价 | 从 swap 事件计算 |
| MA_20m | 20 分钟移动均线 | 从价格快照计算 |

### 6.2 成交量放大

通过 **链上 Aerodrome 池子 Transfer 事件**解析 swap，按以下方式复用 `BuybackMonitor` 的 `eth_getLogs` 模式：

```python
# 复用 BuybackMonitor._check_token() 中的 eth_getLogs 调用模式
logs = await w3.eth.get_logs({
    "address": AERO_POOL_ADDRESS,
    "fromBlock": hex(from_block),
    "toBlock": hex(current_block),
    "topics": [TRANSFER_TOPIC, None, None],
})
```

**不需要引入 subgraph、索引器或外部数据平台**。

| 指标 | 计算方式 |
|------|---------|
| 5m 成交量 | 最近 5 分钟内 swap 交易的金额累计 |
| 平均 5m 成交量 (1h) | 过去 1 小时 12 个 5m 窗口的平均值 |
| 成交量放大倍数 | `volume_5m / avg_volume_5m_1h` |
| 主动买入量 | swap 中 taker 买 AERO 的金额 |
| 主动卖出量 | swap 中 taker 卖 AERO 的金额 |
| 主动买入占比 | `buy_volume / (buy_volume + sell_volume)` |

### 6.3 流动性质量

通过 **OKX quote 模拟**获取，不需要计算池子深度的复杂公式。

| 指标 | 计算方式 |
|------|---------|
| 模拟买入滑点 | `okx.get_quote(USDC, AERO, buy_amount)` 返回 `priceImpactPercent` |
| 模拟卖出滑点 | `okx.get_quote(AERO, USDC, sell_amount)` 返回 `priceImpactPercent` |

OKX DEX API 已返回 `priceImpactPercent`，直接复用 `Trader._validate_quote()` 中的滑点校验逻辑。

### 6.4 买卖方向识别

通过解析 Aerodrome 池的 swap 事件判断：
- `amount0In > 0 && amount1Out > 0` → token1 → token0（如果 token0=AERO，则是在卖出 AERO）
- `amount1In > 0 && amount0Out > 0` → token0 → token1（如果 token0=AERO，则在买入 AERO）

Aerodrome 池合约的 `Swap` 事件 signature：
```solidity
event Swap(
    address indexed sender,
    uint256 amount0In, uint256 amount1In,
    uint256 amount0Out, uint256 amount1Out,
    address indexed to
);
```

---

## 7. 技术架构

```
┌──────────────────────────────────────────────────────────┐
│  src/strategy/aero_collector.py                          │
│  Market Data Collector                                    │
│  ┌─────────────────────────────────────────────────────┐  │
│  │ 从链上获取: price snapshots, swap events, pool data │  │
│  │ 复用: RPCRouter, eth_getLogs, fromBlock 追踪         │  │
│  │    (BuybackMonitor 的数据采集模式)                    │  │
│  └────────────────────┬────────────────────────────────┘  │
│                       ▼                                   │
│  src/strategy/aero_strategy.py                            │
│  Strategy Engine                                          │
│  ┌─────────────────────────────────────────────────────┐  │
│  │ 输入: MarketSnapshot → 输出: TradeDecision           │  │
│  │ 内部: IndicatorEngine (指标计算) + EntryLogic +      │  │
│  │        ExitLogic + RiskFilter                        │  │
│  └────────────────────┬────────────────────────────────┘  │
│                       ▼                                   │
│  src/strategy/aero_position.py                            │
│  Position Manager                                         │
│  ┌─────────────────────────────────────────────────────┐  │
│  │ 输入: TradeDecision → 管理持仓生命周期               │  │
│  │ 状态: entry_price, amount, highest_price, TP levels │  │
│  │ 复用: StateManager + StrategyState                  │  │
│  └──────┬──────────────────────────────────────────────┘  │
│         │                                                 │
├─────────┼──────────────────────────────────────────────────┤
│         ▼                                                 │
│  src/executor/trader.py  (✅ 零改动)                       │
│  trader.buy(AERO_ADDR, amount, USDC_BASE)                 │
│  trader.sell(AERO_ADDR)                                   │
│                                                                │
│  src/risk/guard.py  (✅ 零改动)                            │
│  guard.can_trade() + guard.record_pnl(pnl)                 │
│                                                                │
│  src/db/database.py  (✅ 零改动, strategy='aero_trend')     │
│  insert_buy/insert_sell/get_open_positions/get_trades_by_strategy  │
│                                                                │
│  src/state/persistence.py  (✅ 零改动)                    │
│  strategy_state.is_on_cooldown("aero_trend")               │
│  strategy_state.get_last_run("aero_trend")                 │
└──────────────────────────────────────────────────────────────┘
```

---

## 8. 模块设计

### 8.1 `src/strategy/aero_collector.py` — 数据采集 [新建]

复用 `BuybackMonitor` 的 `eth_getLogs` 轮询 + `fromBlock` 追踪模式，采集 Aerodrome 池子的 swap 事件和池子状态。

```python
class AeroMarketCollector:
    """
    采集 AERO/USDC 链上数据。
    复用 RPCRouter 做 RPC 调用，复用 eth_getLogs 模式轮询 swap 事件。
    """

    def __init__(self, w3: RPCRouter, okx: OKXDexClient, pool_addr: str,
                 aero_addr: str, usdc_addr: str):
        self._w3 = w3
        self._okx = okx
        self._pool = pool_addr
        self._aero = aero_addr
        self._usdc = usdc_addr
        self._from_block: int = 0           # 复用 BuybackMonitor 的 fromBlock 追踪
        self._price_buffer: deque = deque() # 滚动价格窗口

    async def get_current_price(self) -> float | None:
        """复用 okx.get_quote() 获取实时价格"""
        ...

    async def fetch_swap_events(self) -> list[SwapEvent]:
        """
        复用 BuybackMonitor._check_token() 中的 eth_getLogs 调用方式，
        只改 topics 过滤条件和事件解析逻辑。
        """
        ...

    def compute_vwap(self, swaps: list[SwapEvent]) -> float:
        """从 swap 事件计算 VWAP"""
        ...

    async def get_pool_liquidity(self) -> float:
        """通过池子合约 reserve 方法获取流动性"""
        ...
```

### 8.2 `src/strategy/aero_strategy.py` — 策略引擎 [新建]

核心策略逻辑，保持纯函数式指标计算，易于测试。

```python
@dataclass
class MarketSnapshot:
    """一次市场快照"""
    price: float
    return_5m: float
    return_15m: float
    return_30m: float
    return_1h: float
    vwap: float
    ma_20m: float
    volume_5m: float
    avg_volume_5m_1h: float
    volume_ratio: float
    buy_pressure: float
    pool_liquidity_usd: float
    simulated_buy_slippage: float
    simulated_sell_slippage: float
    # 以下字段用于强势回踩判断
    price_above_open_1h: bool
    pullback_from_high: float
    sell_pressure_declining: bool


@dataclass
class TradeDecision:
    action: Literal["BUY", "SELL", "HOLD"]
    reason: str
    strategy_type: str | None = None  # "breakout_momentum" | "strong_pullback"
    pct_to_sell: float | None = None  # 分批止盈用


class IndicatorEngine:
    """
    纯函数指标计算，无副作用。
    """
    @staticmethod
    def compute_returns(prices: list[float]) -> dict[str, float]:
        """计算各周期涨幅"""
        ...

    @staticmethod
    def compute_volume_ratio(volume_5m: float, avg_volume_5m_1h: float) -> float:
        return volume_5m / avg_volume_5m_1h if avg_volume_5m_1h > 0 else 0

    @staticmethod
    def compute_buy_pressure(buy_vol: float, sell_vol: float) -> float:
        ...

    @staticmethod
    def compute_vwap(swaps: list) -> float:
        """量加权平均价"""
        ...

    @staticmethod
    def compute_ma(prices: list[float], window: int) -> float:
        ...

    @staticmethod
    def compute_pullback(current_price: float, recent_high: float) -> float:
        return (recent_high - current_price) / recent_high


class TrendStrategy:
    """
    趋势策略：输入 MarketSnapshot + 持仓状态 → 输出 TradeDecision。
    纯函数逻辑，不涉及链上调用，方便单元测试。
    """

    def evaluate_entry(self, snap: MarketSnapshot, pos_mgr) -> TradeDecision | None:
        """
        评估买入条件：
        1. 趋势启动型（11 条件全满足）
        2. 强势回踩型（11 条件全满足）
        """
        ...

    def evaluate_exit(self, snap: MarketSnapshot, pos) -> TradeDecision | None:
        """
        按优先级评估卖出条件：
        1. 硬止损 (P0)
        2. 信号反转 (P1)
        3. 时间止损 (P2)
        4. 分批止盈 (P3)
        5. 移动止盈 (P4)
        """
        ...
```

### 8.3 `src/strategy/aero_position.py` — 持仓管理 [新建]

```python
@dataclass
class AeroPosition:
    """AERO 持仓状态"""
    has_position: bool = False
    entry_price: float = 0.0
    current_price: float = 0.0
    position_amount: float = 0.0    # AERO 数量
    position_value_usdc: float = 0.0
    cost_basis_usdc: float = 0.0
    pnl_pct: float = 0.0
    highest_price_since_entry: float = 0.0
    holding_time_minutes: int = 0
    take_profit_1_done: bool = False   # 10% TP
    take_profit_2_done: bool = False   # 20% TP
    trailing_stop_active: bool = False
    consecutive_losses: int = 0        # 连续亏损计数
    entry_time: datetime | None = None

    def update_price(self, price: float) -> None:
        """更新当前价，追踪最高价，计算浮盈"""
        ...

    def drawdown_from_peak(self) -> float:
        """当前从最高点回撤比例"""
        ...


class PositionManager:
    """
    持仓管理器。状态通过 StateManager 持久化。
    复用 src/state/persistence.py 的 StrategyState 做冷却期判断。
    """

    def __init__(self, state_mgr: StateManager, strategy_state: StrategyState,
                 guard: DailyLossGuard, db_path: str = DB_PATH):
        self._state = state_mgr
        self._strategy_state = strategy_state
        self._guard = guard
        self._pos = AeroPosition()

    def load(self) -> None:
        """从 state.json 恢复持仓"""

    def save(self) -> None:
        """持久化持仓"""

    def is_on_cooldown(self) -> bool:
        """复用 StrategyState.is_on_cooldown()"""

    def record_trade_result(self, pnl_pct: float) -> None:
        """
        记录交易结果并更新连续亏损计数。
        复用 get_trades_by_strategy('aero_trend') 获取最近 N 笔 PnL。
        """
        ...
```

---

## 9. 现有模块配置变更

### 9.1 `src/config/loader.py` — 加 `AeroTrendConfig`

仿照 `GridConfig` 模式：

```python
@dataclass
class AeroTrendConfig:
    enabled: bool = False
    pool_address: str = ""
    aero_address: str = "0x940181a94a35a4569e4529a3cdfb74e38fd98631"
    usdc_address: str = "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913"
    # 买入条件
    min_return_5m: float = 0.02       # 5m 涨幅下限
    max_return_5m: float = 0.08       # 5m 涨幅上限
    min_return_15m: float = 0.04      # 15m 涨幅下限
    max_return_30m: float = 0.40      # 30m 涨幅上限（过热禁止）
    min_volume_ratio: float = 3.0     # 成交量放大倍数
    min_buy_pressure: float = 0.65    # 主动买入占比
    min_liquidity_usd: float = 200000 # 最小流动性
    max_slippage_buy: float = 0.01    # 买入最大滑点
    # 卖出条件
    stop_loss_pct: float = 0.07
    time_stop_minutes: int = 60
    time_stop_min_profit: float = 0.03
    take_profit_1_pct: float = 0.10
    take_profit_1_ratio: float = 0.30
    take_profit_2_pct: float = 0.20
    take_profit_2_ratio: float = 0.30
    trailing_stop_drawdown: float = 0.08
    # 仓位
    position_size_pct: float = 0.05
    position_size_reduced: float = 0.025
    consecutive_loss_limit: int = 5      # 连续亏损上限
    daily_profit_cap: float = 0.08      # 进入保守模式
    daily_loss_limit_pct: float = 0.03  # 当日停止交易
    cooldown_minutes: int = 30
    poll_interval_sec: float = 60
```

在 `Config` 类中增加字段：
```python
aero_trend: AeroTrendConfig = field(default_factory=AeroTrendConfig)
```

在 `_parse_yaml` 中增加解析。

### 9.2 `config.yaml` — 增加段

```yaml
aero_trend:
  enabled: false
  pool_address: "0xcDdac48af89589052Ff14A3cACF58596fE7E2Be2"
  poll_interval_sec: 60
  position_size_pct: 0.05
  # 其余使用默认值
```

Aerodrome AERO/USDC 池子地址：`0xcDdac48af89589052Ff14A3cACF58596fE7E2Be2`（Base mainnet）。

### 9.3 `src/main.py` — 加 `aero_loop`

仿照现有 `grid_loop` 模式：

```python
# 在 run() 中
aero_strategy_ok = False
if cfg.aero_trend.enabled:
    from src.strategy.aero_collector import AeroMarketCollector
    from src.strategy.aero_strategy import TrendStrategy
    from src.strategy.aero_position import PositionManager

    collector = AeroMarketCollector(w3, okx, cfg.aero_trend.pool_address, ...)
    strategy = TrendStrategy(cfg.aero_trend)
    pos_mgr = PositionManager(state_mgr, strategy_state, guard)
    aero_strategy_ok = True

async def aero_loop(stop):
    if not aero_strategy_ok:
        return
    while not stop.is_set():
        try:
            snap = await collector.collect()
            pos_mgr.load()
            decision = strategy.evaluate_entry(snap, pos_mgr) \
                     or strategy.evaluate_exit(snap, pos_mgr.position)
            if decision:
                await execute_decision(decision, trader, pos_mgr, ...)
            pos_mgr.save()
        except Exception as e:
            logger.error("[AERO] tick 异常: %s", e)
        await asyncio.sleep(cfg.aero_trend.poll_interval_sec)
```

---

## 10. 开发优先级

### Phase 1 ✅ — 配置 + 数据采集（3 天）

| 任务 | 文件 | 工作量 |
|------|------|--------|
| 加 `AeroTrendConfig` + `_parse_aero` | `config/loader.py` | ~30 行, 30min |
| 加 `config.yaml` 段 | `config.yaml` | ~15 行, 5min |
| 实现 `AeroMarketCollector`（价格、swap 事件、池子流动性） | `src/strategy/aero_collector.py` | ~150 行, 2h |
| 容器化调试 | — | — |

**验证**：`python src/main.py --dry-run` 不报配置错。单独运行 collector 能打印出 AERO 实时价格和成交量数据。

### Phase 2 ✅ — 策略引擎（2 天）

| 任务 | 文件 | 工作量 |
|------|------|--------|
| 实现 `IndicatorEngine`（纯函数指标） | `src/strategy/aero_strategy.py` | ~100 行 |
| 实现 `TrendStrategy.evaluate_entry`（趋势启动型 + 强势回踩型） | 同上 | ~80 行 |
| 实现 `TrendStrategy.evaluate_exit`（5 种卖出） | 同上 | ~100 行 |

**验证**：纯函数测试，`pytest tests/ -x` 通过。给一组快照数据能输出正确决策。

### Phase 3 ✅ — 纸面交易（2 天）

| 任务 | 文件 | 工作量 |
|------|------|--------|
| 实现 `PositionManager`（状态持久化、连续亏损、冷却期） | `src/strategy/aero_position.py` | ~120 行 |
| 实现 `AeroPosition` 持仓追踪 | 同上 | ~80 行 |
| 组装 `aero_loop` 并接入纸面模式 | `main.py` | ~60 行 |

**验证**：`python src/main.py --dry-run` 运行 24h 以上，完整记录所有市场快照+决策+虚拟交易。

### Phase 4 ✅ — 实盘（1 天）

| 任务 | 工作量 |
|------|--------|
| 取消 dry-run，接入真实 Trader | 配置改 1 行 |
| 验证卖出（止损/止盈/时间止损）路径 | — |
| 配置飞书告警（复用现有） | — |

**验证**：小额实盘，观察 3 笔以上完整交易闭环。

---

## 11. 开发总清单

### 新建文件

| 文件 | 行数预估 | 说明 |
|------|---------|------|
| `src/strategy/aero_collector.py` | ~200 | 数据采集，复用 eth_getLogs 模式 |
| `src/strategy/aero_strategy.py` | ~350 | 指标计算 + 策略逻辑（纯函数） |
| `src/strategy/aero_position.py` | ~200 | 持仓管理 + 状态持久化 |

**总计新增代码：约 750 行**，而非从零开始的数千行。核心复用比例为 **~70% 现有基础设施**。

### 修改文件

| 文件 | 行数 | 改动 |
|------|------|------|
| `src/config/loader.py` | +30 | 加 dataclass + 解析 |
| `config.yaml` | +20 | 加配置段 |
| `src/main.py` | +60 | 加 aero_loop 协程 |
| `src/web/api.py` | +30 | 加 AERO 策略状态、控制端点 |

**总计改动：约 140 行**。

---

## 12. 成功指标

| 阶段 | 标准 |
|------|------|
| S1 | 系统连续运行 7 天，完整记录所有市场快照、交易信号和结果 |
| S2 | 纸面交易所有交易都能追溯到明确规则 |
| S3 | 策略触发后 30min/1h/4h 平均收益高于随机买入 |
| S4 | 连续 20 笔交易后最大回撤在预设范围内 |
| S5 | 系统能在亏损日自动降低风险，连续亏损后自动停止 |

---

## 13. Aerodrome 池子信息（Base Mainnet）

| 参数 | 值 |
|------|-----|
| AERO 代币 | `0x940181a94a35a4569e4529a3cdfb74e38fd98631` |
| USDC (Bridged) | `0x833589fcd6edb6e08f4c7c32d4f71b54bda02913` |
| AERO/USDC 池 | `0xcDdac48af89589052Ff14A3cACF58596fE7E2Be2` |
| Aerodrome Factory | `0x420DD381b31aEf6683db6B902084cB0FFECe40Da` |
| 链 ID | 8453 (Base) |

---

## 14. 关键风险与控制

| 风险 | 控制手段 |
|------|---------|
| 追高 | 5m 涨幅 ≥ 15% 或 30m 涨幅 ≥ 40% 禁止买入（配置可调） |
| 成交量假放大 | 买卖压力过滤（需 ≥ 0.65），后续可再加地址画像 |
| 滑点 | 交易前必须模拟滑点，超过阈值跳过（复用 `_validate_quote`） |
| 止损执行失败 | 卖出重试 3 次，失败发送高优先级告警 |
| 震荡行情连续止损 | 连续亏损降仓 + 冷却期 + 单日亏损上限（复用 guard + StrategyState） |
| RPC 失败 | 已有 RPCRouter 自动主备切换（完全复用） |
| OKX API 失败 | 已有重试 + 日志（完全复用） |
| 交易失败 | 买入重试 ≤ 1 次，卖出重试 ≤ 3 次 |
| 池子深度不足 | 流动性 < 200K USD 跳过，模拟滑点阈值保障 |

---

## 15. 后续扩展

- 单标的稳定 → 扩展到 VIRTUAL / BRETT / TOSHI / DEGEN
- 多标的稳定 → 叙事轮动
- 链上数据积累 → holder 增长、聪明钱地址、大户行为分析
- 交易执行稳定 → 回测模块
