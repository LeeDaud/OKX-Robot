import type { AppConfig, WalletInfo, TradeRecord, TradeStats, Position, PositionAllResponse, CopyTarget } from '@/types/api'

const BASE = '/api'

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  })
  if (!res.ok) {
    const body = await res.json().catch(() => ({}))
    throw new Error(body.detail || `HTTP ${res.status}`)
  }
  return res.json()
}

// Config
export function fetchConfig(): Promise<AppConfig> {
  return request('/config')
}

export function addTarget(target: Partial<CopyTarget>): Promise<{ ok: boolean }> {
  return request('/config/targets', {
    method: 'POST',
    body: JSON.stringify(target),
  })
}

export function updateTarget(address: string, target: Partial<CopyTarget>): Promise<{ ok: boolean }> {
  return request(`/config/targets/${address}`, {
    method: 'PUT',
    body: JSON.stringify(target),
  })
}

export function deleteTarget(address: string): Promise<{ ok: boolean }> {
  return request(`/config/targets/${address}`, { method: 'DELETE' })
}

export function fetchWallet(): Promise<WalletInfo> {
  return request('/config/wallet')
}

export function updateWallet(wallet: Partial<WalletInfo & { private_key?: string; okx_api_key?: string; okx_secret_key?: string; okx_passphrase?: string }>): Promise<{ ok: boolean; updated: string[] }> {
  return request('/config/wallet', {
    method: 'PUT',
    body: JSON.stringify(wallet),
  })
}

export function updateParams(params: Record<string, unknown>): Promise<{ ok: boolean }> {
  return request('/config/params', {
    method: 'PUT',
    body: JSON.stringify(params),
  })
}

// Trades
export function fetchTrades(limit = 50, offset = 0): Promise<{ trades: TradeRecord[] }> {
  return request(`/trades?limit=${limit}&offset=${offset}`)
}

export function fetchTradeStats(): Promise<TradeStats> {
  return request('/trades/stats')
}

export function fetchPositions(): Promise<{ positions: Position[] }> {
  return request('/positions')
}

export function fetchPositionsAll(): Promise<PositionAllResponse> {
  return request('/positions/all')
}

export function refreshPositionsPrices(): Promise<{
  tokens: Record<string, { symbol: string | null; decimals: number; current_price: number | null }>
  positions: Record<string, {
    amount: number
    cost_basis_usd: number
    current_price: number | null
    current_value_usd: number | null
    unrealized_pnl: number | null
    roi_pct: number | null
  }>
  error?: string
}> {
  return request('/positions/refresh-prices', { method: 'POST' })
}
