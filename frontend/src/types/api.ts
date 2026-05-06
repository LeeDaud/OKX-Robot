export interface CopyTarget {
  address: string
  remark?: string
  trade_mode?: string
  trade_ratio?: number
  trade_fixed_usd?: number
  trade_max_usd?: number
  trade_fixed_virtuals?: number
}

export interface AppConfig {
  base_token: string
  trade_mode: string
  trade_ratio: number
  trade_fixed_usd: number
  trade_max_usd: number
  trade_fixed_virtuals: number
  token_whitelist: string[]
  min_trade_usd: number
  daily_loss_limit_usd: number
  slippage: number
  gas_limit_gwei: number
  take_profit_roi: number
  take_profit_check_sec: number
  dry_run: boolean
  poll_interval_sec: number
  wallet_address: string
  copy_targets: CopyTarget[]
  buyback_watch: Record<string, string>

  // 结构化分组（可选，向后兼容）
  wallet?: WalletConfig
  copy_trading?: CopyTradingConfig
  grid?: GridConfigInfo
}

export interface WalletConfig {
  wallet_address: string
  rpc_http_url: string
  rpc_ws_url?: string
  has_private_key: boolean
  has_okx_api_key: boolean
  dry_run: boolean
  base_token: string
}

export interface CopyTradingConfig {
  enabled: boolean
  trade_mode: string
  trade_ratio: number
  trade_fixed_usd: number
  trade_max_usd: number
  trade_fixed_virtuals: number
  token_whitelist: string[]
  min_trade_usd: number
  daily_loss_limit_usd: number
  slippage: number
  gas_limit_gwei: number
  take_profit_roi: number
  take_profit_check_sec: number
  poll_interval_sec: number
  targets: CopyTarget[]
}

export interface GridConfigInfo {
  enabled: boolean
  token: string
  levels: number
  spread_pct: number
  investment_usdc: number
  profit_pct: number
}

export interface WalletInfo {
  wallet_address: string
  rpc_http_url: string
  rpc_ws_url?: string
  has_private_key?: boolean
  has_okx_api_key?: boolean
}

export interface TradeRecord {
  id: number
  source_tx: string
  source_addr: string
  token_in: string
  token_out: string
  amount_in: string
  amount_out: number
  our_tx: string | null
  status: string
  side: string
  position_id: string | null
  entry_price: number
  exit_price: number
  roi_pct: number
  pnl: number
  created_at: string
  filled_amount?: string
  filled_cost_usd?: number
  strategy: string
}

export type StrategyFilter = 'all' | 'copy' | 'grid' | 'dca' | 'buyback' | 'aero_trend'

// ── AERO 趋势策略类型 ────────────────────────────────────

export interface AeroState {
  has_position: boolean
  entry_price: number
  current_price: number
  position_amount: number
  cost_basis_usdc: number
  pnl_pct: number
  highest_price: number
  holding_time_minutes: number
  take_profit_1_done: boolean
  take_profit_2_done: boolean
  trailing_stop_active: boolean
  consecutive_losses: number
  entry_time: string
  buy_tx_hash: string
}

export interface TradeStats {
  today: { total: number; success: number; pnl: number }
  all: { total_trades: number; total_invested: number; realized_pnl: number }
  today_pnl: number
}

export interface PositionRecord {
  id: number
  source_tx: string
  source_addr: string
  token_in: string
  token_out: string
  amount_in: string
  amount_out: number
  entry_price: number
  exit_price: number
  roi_pct: number
  pnl: number
  status: string
  created_at: string
  filled_cost_usd?: number
}

export interface PositionAllResponse {
  open: PositionRecord[]
  closed: PositionRecord[]
  summary: {
    open_count: number
    closed_count: number
    total_invested_open: number
    realized_pnl: number
  }
}

export interface Position {
  [key: string]: any
}

export interface BalancesResponse {
  balances: Record<string, number | null>
  base_token: string
  wallet_address: string
  error?: string
}

// ── Grid 策略类型 ─────────────────────────────────────────

export interface GridSlotData {
  slot_id: number
  buy_price: number
  sell_price: number
  amount_usdc: number
  status: "idle" | "bought"
  filled_amount: number
  current_value_usd: number | null
  unrealized_pnl: number | null
  roi_pct: number | null
}

export interface GridState {
  enabled: boolean
  token: string
  token_symbol: string
  current_price: number | null
  total_investment: number
  total_slots: number
  active_slots: number
  realized_pnl: number
  unrealized_pnl: number
  total_pnl: number
  volatility_adjust: boolean
  slots: GridSlotData[]
  updated_at: string
}

export interface GridTradeRecord {
  tx_hash: string
  side: "buy" | "sell"
  token_address: string
  amount_out_raw: number
  amount_in_raw: number
  cost_usd: number
  pnl_usd: number
  roi_pct: number
  created_at: string
}

export interface GridHistoryResponse {
  trades: GridTradeRecord[]
}
