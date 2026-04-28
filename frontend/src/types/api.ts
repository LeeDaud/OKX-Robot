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
}

export interface TradeStats {
  today: { total: number; success: number; pnl: number }
  all: { total_trades: number; total_invested: number; realized_pnl: number }
  today_pnl: number
}

export interface PositionRecord {
  id: number
  source_tx: string
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
