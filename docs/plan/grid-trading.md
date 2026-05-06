# 套利策略升级方案 — 网格交易（Grid Trading）

## 问题

当前策略组合交易频率太低：
- **DCA 定投**：每日一次，买入低频
- **回购套利**：依赖项目方行为，机会不可控
- **止盈**：只卖不买，单向

结果是机器人大部分时间空闲，无法持续产生交易和收益。

## 方案：网格交易（Grid Trading）

核心思路：**在价格网格的不同价位挂单，跌到位就买，涨到位就卖，循环套利。**

```
价格 ↑
1.10 ── 卖出（利润 +3%） ← slot 1 卖出
1.08 ── 卖出（利润 +3%） ← slot 2 卖出
1.06 ── 卖出（利润 +3%） ← slot 3 卖出
1.04 ── 卖出（利润 +3%）
1.02 ── 卖出（利润 +3%）
1.00 ── 当前价格 ────────
0.98 ── 买入 ──────────── slot 5 买入
0.96 ── 买入              slot 6 买入
0.94 ── 买入
0.92 ── 买入
0.90 ── 买入
价格 ↓
```

每个网格 slot 的状态机：**空闲 → 买入成交 → 等待卖出 → 卖出成交 → 回到空闲**

价格波动越频繁，网格成交次数越多。横盘和震荡市尤其适合。

## 新增文件

| 文件 | 说明 |
|------|------|
| `src/strategy/grid.py` | 网格策略的核心逻辑：状态管理、价位计算、触发执行 |

## 修改文件

| 文件 | 改动 |
|------|------|
| `src/config/loader.py` | 新增 GridConfig dataclass |
| `src/main.py` | 新增 grid_loop 协程，在主循环中启动 |
| `config.yaml` | 新增 grid 策略配置段 |
| `tests/test_risk.py` | 保持不变，仍有效 |

## 无需修改

`trader.py`、`okx_client.py`、`rpc/router.py`、`risk/guard.py`、`monitor/buyback.py`、`state/persistence.py`、`notify/feishu.py` 均不变，直接复用。

## GridStrategy 设计

### 数据结构

```python
@dataclass
class GridSlot:
    """一个网格位。"""
    slot_id: int               # 位号 0..N-1
    buy_price: float           # 触发买入的 USDC 价格
    sell_price: float          # 触发卖出的 USDC 价格
    amount_usdc: float         # 此位的 USDC 投入
    status: str = "idle"       # idle | bought | sold
    buy_tx: str = ""           # 买入交易哈希
    sell_tx: str = ""          # 卖出交易哈希
    filled_amount: int = 0     # 买入成交数量(raw)
    created_at: str = ""
```

### 初始化

1. 通过 OKX `get_quote` 获取当前 token 价格
2. 以当前价格为基准，生成 N 个买单价位（等比间距向下）
3. 每个 slot 的 `sell_price = buy_price * (1 + grid_profit_pct)`
4. 将所有 slot 持久化到 state.json

### 主循环（每 10 秒执行）

```
for each slot in grid:
  if slot.status == 'idle' and current_price <= slot.buy_price:
    → execute BUY
    → slot.status = 'bought'
    → 记录 filled_amount, buy_tx
    → 持久化状态

  if slot.status == 'bought' and current_price >= slot.sell_price:
    → execute SELL (卖 filled_amount 个代币)
    → slot.status = 'sold'
    → 记录 sell_tx
    → 写入 DB（insert_sell）
    → slot 回到 idle（可再次触发）
    → 持久化状态
```

### 价格获取

通过 OKX `get_quote(TOKEN, USDC, 1e18)` 估算当前价：
```python
quote = await okx.get_quote(token, USDC_BASE, 10**18)
if quote:
    price = float(quote.get("toTokenAmount", 0)) / 1e6
```

### 网格参数计算

```python
def init_grid(current_price, levels, spread_pct, investment_usdc, profit_pct):
    """生成 N 个等间距买单位，向上等比 sell_price。"""
    slots = []
    per_slot = investment_usdc / levels
    
    for i in range(levels):
        buy_price = current_price * (1 - spread_pct * (i + 1) / 100)
        sell_price = buy_price * (1 + profit_pct / 100)
        slots.append(GridSlot(
            slot_id=i,
            buy_price=round(buy_price, 6),
            sell_price=round(sell_price, 6),
            amount_usdc=per_slot,
        ))
    return slots
```

## 配置变更

```yaml
grid:
  enabled: true
  token: "0xc2bceb0ee69455da32abb10a5ba81c0299a925c8"   # 交易代币
  levels: 6                     # 网格位数量
  spread_pct: 2.0               # 相邻位间距（%）
  investment_usdc: 60           # 总投入（均分到每个位）
  profit_pct: 3.0               # 每个位的目标利润率
```

## 与现有策略的兼容

| 策略 | 关系 |
|------|------|
| DCA 定投 | 保留，网格补充买入 |
| 回购套利 | 保留，卖出时通过 DB 关闭对应持仓 |
| 止盈监控 | 调整：网格位由网格自身管理，TP 只负责非网格持仓 |
| 风控 | 网格同样受 `daily_loss_limit_usd` 和 `gas_limit_gwei` 约束 |
| 飞书通知 | 网格买卖通过 `notify_trade` 通知 |

## 验证方案

1. **Dry-run 模式启动**：观察网格初始化、价位计算正确
```
[GRID] 初始化: token=0x... price=$1.23 levels=6 spread=2% invest=$60
[GRID]  slot 0: buy=$1.21 sell=$1.24
[GRID]  slot 1: buy=$1.19 sell=$1.22
...
[GRID] 运行中: 2 slots active, 0 filled, current_price=$1.22
```

2. **用已知低价测试买入触发**：手动将 `buy_price` 设高，观察买入是否触发

3. **Mock 测试**：`PYTHONPATH=. python -m pytest tests/ -x` 通过

## 执行顺序

1. 新增 `src/strategy/grid.py`
2. 修改 `src/config/loader.py`（GridConfig）
3. 修改 `config.yaml`
4. 修改 `src/main.py`（启动 grid_loop）
5. 运行验证
