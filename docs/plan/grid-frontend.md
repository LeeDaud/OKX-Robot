# 网格策略前端面板规划

## 现状

前端（React 19 + Tailwind CSS 4 + Radix UI）存在，但后端 API（旧 `src/web/`）已被删除，前端目前无数据源。

## 方案：新增轻量后端 + 网格页面

### 1. 后端（新增 `src/web/api.py`）

使用 `aiohttp`（已有依赖，无需新增）起一个轻量 HTTP 服务，读取 SQLite + state.json 暴露 REST API：

| 端点 | 说明 | 数据来源 |
|------|------|---------|
| `GET /api/grid/state` | 网格当前状态 | `state.json` → `grid_slots` |
| `GET /api/grid/history` | 网格交易历史 | `trades.db` WHERE strategy LIKE 'grid%' |
| `GET /api/grid/prices` | 当前 AERO 报价 + 各 slot 盈亏 | OKX get_quote + 链上余额 |
| `GET /api/dashboard/stats` | 概览统计 | `trades.db` 聚合查询 |
| `GET /api/positions/open` | 当前持仓 | `trades.db` WHERE is_open=1 |
| `GET /api/wallet/balance` | 钱包余额 | RPC balanceOf |

端口：**8911**（与现有项目端口不冲突）。

启动方式：`src/main.py` 中可选的 `--serve` 参数启动 HTTP 服务，或者独立 `python src/web/api.py` 运行。

### 2. 前端新增页面：Grid.tsx

沿用现有 UI 组件（PageHeader、MetricCard、SectionCard、Table），新增 `src/pages/Grid.tsx`。

#### 页面布局

```
┌─────────────────────────────────────────────────────────┐
│ PageHeader: 网格交易 | 网格策略运行状态与收益监控         │
├─────────────────────────────────────────────────────────┤
│ ┌──────┐ ┌──────┐ ┌──────┐ ┌──────┐                    │
│ │总投资 │ │活跃slot│ │已成交  │ │总PnL  │                    │
│ │$8.00  │ │  2/4  │ │  3次  │ │+$0.12 │                    │
│ └──────┘ └──────┘ └──────┘ └──────┘                    │
├─────────────────────────────────────────────────────────┤
│ 网格可视化（核心组件）                                    │
│                                                         │
│  价格 ↑                                                 │
│  0.485 ──   已买  ●━━━━━○ 卖出价 $0.485    ← slot 0    │
│  0.471 ── 当前价 ◆                                        │
│  0.456 ──   待买  ○ 买入价 $0.456          ← slot 1    │
│  0.442 ──   待买  ○ 买入价 $0.442          ← slot 2    │
│  0.429 ──   待买  ○ 买入价 $0.429          ← slot 3    │
│  价格 ↓                                                 │
│                                                         │
│  图例：● 已买入  ○ 空闲  ◆ 当前价                        │
├─────────────────────────────────────────────────────────┤
│ Slot 明细表格                                           │
│ ┌────┬──────┬──────┬───────┬──────┬─────┬──────┐        │
│ │ slot│买入价 │卖出价 │投入USDC│状态  │PnL  │ROI   │        │
│ ├────┼──────┼──────┼───────┼──────┼─────┼──────┤        │
│ │  0 │$0.456│$0.470│ $2.00 │bought│+0.03│+1.5% │        │
│ │  1 │$0.442│$0.455│ $2.00 │ idle │  -  │  -   │        │
│ │  2 │$0.429│$0.442│ $2.00 │ idle │  -  │  -   │        │
│ │  3 │$0.416│$0.429│ $2.00 │ idle │  -  │  -   │        │
│ └────┴──────┴──────┴───────┴──────┴─────┴──────┘        │
├─────────────────────────────────────────────────────────┤
│ 网格交易历史                                            │
│ ┌──────┬──────┬──────┬─────┬──────┬──────────┐          │
│ │方向  │数量  │价格  │PnL  │ROI   │时间      │          │
│ ├──────┼──────┼──────┼─────┼──────┼──────────┤          │
│ │ 买入 │4.24A │$0.456│  -  │  -   │ 16:32:15 │          │
│ │ 卖出 │4.24A │$0.470│+0.03│+1.5% │ 17:05:42 │          │
│ └──────┴──────┴──────┴─────┴──────┴──────────┘          │
└─────────────────────────────────────────────────────────┘
```

#### 网格可视化组件

新增 `src/components/GridVisualizer.tsx`：

- 竖轴为价格刻度
- 每个 slot 用横线表示（买入价 → 卖出价区间）
- 状态通过颜色区分（绿色=bought，灰色=idle）
- 当前价格用菱形 ◆ 标记
- 响应式：桌面全宽显示，移动端竖排

#### 样式

完全复用现有 CSS 变量体系（`--primary`, `--success`, `--border` 等），不新增自定义颜色。

### 3. 导航变更

Shell.tsx navItems 新增一项：

```tsx
{ to: "/grid", label: "网格策略", icon: Grid3x3 }
```

### 4. 新增类型

`types/api.ts` 新增：

```typescript
export interface GridSlotData {
  slot_id: number
  buy_price: number
  sell_price: number
  amount_usdc: number
  status: "idle" | "bought"
  buy_tx: string
  filled_amount: number
  current_price: number | null
  current_value_usd: number | null
  unrealized_pnl: number | null
  roi_pct: number | null
}

export interface GridState {
  enabled: boolean
  token: string
  token_symbol: string
  current_price: number
  total_investment: number
  active_slots: number
  total_slots: number
  total_pnl: number
  slots: GridSlotData[]
}

export interface GridTradeRecord {
  tx_hash: string
  side: "buy" | "sell"
  token_address: string
  amount: number
  price: number
  cost_usd: number
  pnl_usd: number
  roi: number
  created_at: string
}
```

### 5. API 函数

`lib/api.ts` 新增：

```typescript
export function fetchGridState(): Promise<GridState>
export function fetchGridHistory(): Promise<{ trades: GridTradeRecord[] }>
```

### 6. 新增文件清单

| 文件 | 类型 | 说明 |
|------|------|------|
| `src/web/__init__.py` | 后端 | 空包 |
| `src/web/api.py` | 后端 | aiohttp 轻量 HTTP API（~150 行） |
| `frontend/src/pages/Grid.tsx` | 前端 | 网格交易面板页 |
| `frontend/src/components/GridVisualizer.tsx` | 前端 | 网格价位可视化组件 |
| `frontend/src/components/ui/grid-3x3.tsx` | 前端 | (可选) lucide-react 已有 Grid3x3 图标 |

### 7. 修改文件清单

| 文件 | 改动 |
|------|------|
| `frontend/src/Shell.tsx` | navItems 新增网格条目 |
| `frontend/src/App.tsx` | 新增 Grid 路由 |
| `frontend/src/lib/api.ts` | 新增 grid API 函数 |
| `frontend/src/types/api.ts` | 新增 grid 类型 |
| `frontend/src/lib/tokens.ts` | 添加 AERO 代币名称映射 |
| `src/main.py` | (可选) 新增 `--serve` 参数启动 API |

### 8. 执行顺序

1. 创建 `src/web/api.py`（轻量后端 + 所有端点）
2. 新增 `types/api.ts` 类型
3. 新增 `lib/api.ts` 函数
4. 新增 `src/components/GridVisualizer.tsx`
5. 新增 `src/pages/Grid.tsx`
6. 修改 `Shell.tsx` + `App.tsx`（导航 + 路由）
7. 修改 `tokens.ts`（添加 AERO）
8. 前端 `npm run build` 验证
9. 启动 `api.py` + 前端预览验证
