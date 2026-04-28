import { useQuery } from '@tanstack/react-query'
import { fetchTrades, fetchTradeStats } from '../lib/api'

export default function Trades() {
  const { data: tradesData } = useQuery({ queryKey: ['trades'], queryFn: () => fetchTrades(100, 0) })
  const { data: stats } = useQuery({ queryKey: ['stats'], queryFn: fetchTradeStats })

  const trades = tradesData?.trades ?? []

  return (
    <div>
      <h1 style={{ fontSize: 22, fontWeight: 700, margin: '0 0 20px' }}>交易记录</h1>

      {stats && (
        <div style={{ display: 'flex', gap: 16, marginBottom: 20, fontSize: 13 }}>
          <span>今日: <strong>{stats.today.total}</strong> 笔 | 成功 <strong>{stats.today.success}</strong> | PnL <strong style={{ color: stats.today_pnl >= 0 ? 'var(--success)' : 'var(--danger)' }}>${stats.today_pnl.toFixed(2)}</strong></span>
        </div>
      )}

      <div className="card" style={{ overflowX: 'auto' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
          <thead>
            <tr style={{ color: 'var(--text-secondary)', textAlign: 'left' }}>
              <th style={{ padding: '8px 8px' }}>时间</th>
              <th style={{ padding: '8px 8px' }}>方向</th>
              <th style={{ padding: '8px 8px' }}>状态</th>
              <th style={{ padding: '8px 8px' }}>Token In</th>
              <th style={{ padding: '8px 8px' }}>Token Out</th>
              <th style={{ padding: '8px 8px' }}>PnL</th>
              <th style={{ padding: '8px 8px' }}>ROI</th>
            </tr>
          </thead>
          <tbody>
            {trades.length === 0 && (
              <tr><td colSpan={7} style={{ padding: 20, textAlign: 'center', color: 'var(--text-muted)' }}>暂无交易记录</td></tr>
            )}
            {trades.map((t) => (
              <tr key={t.id} style={{ borderTop: '1px solid var(--border)' }}>
                <td style={{ padding: '8px', whiteSpace: 'nowrap' }}>{t.created_at?.slice(11, 19) || '-'}</td>
                <td style={{ padding: '8px' }}>
                  <span style={{ color: t.side === 'buy' ? 'var(--success)' : 'var(--danger)', fontWeight: 600 }}>
                    {t.side === 'buy' ? '买入' : '卖出'}
                  </span>
                </td>
                <td style={{ padding: '8px' }}>
                  <span style={{
                    display: 'inline-block', padding: '1px 6px', borderRadius: 4, fontSize: 11,
                    background: t.status === 'success' ? 'var(--success)' : t.status === 'pending' ? 'var(--warning)' : 'var(--danger)',
                    color: '#fff', opacity: 0.85,
                  }}>
                    {t.status}
                  </span>
                </td>
                <td style={{ padding: '8px', fontFamily: 'monospace', fontSize: 11 }}>{t.token_in?.slice(0, 10) || '-'}...</td>
                <td style={{ padding: '8px', fontFamily: 'monospace', fontSize: 11 }}>{t.token_out?.slice(0, 10) || '-'}...</td>
                <td style={{ padding: '8px', color: (t.pnl || 0) >= 0 ? 'var(--success)' : 'var(--danger)', fontWeight: 600 }}>
                  {t.pnl != null ? `$${t.pnl.toFixed(2)}` : '-'}
                </td>
                <td style={{ padding: '8px' }}>{t.roi_pct != null ? `${(t.roi_pct * 100).toFixed(1)}%` : '-'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
