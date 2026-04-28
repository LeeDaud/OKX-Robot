import type { AppConfig, WalletInfo, TradeRecord, TradeStats, Position, CopyTarget } from '../types/api'

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
