import { useQuery } from '@tanstack/react-query'
import { fetchConfig, fetchTradeStats, fetchPositions } from '../lib/api'

export default function Dashboard() {
  const { data: config } = useQuery({ queryKey: ['config'], queryFn: fetchConfig })
  const { data: stats } = useQuery({ queryKey: ['stats'], queryFn: fetchTradeStats })
  const { data: positions } = useQuery({ queryKey: ['positions'], queryFn: fetchPositions })

  const openCount = positions?.positions?.length ?? 0

  return (
    <div>
      <h1 style={{ fontSize: 22, fontWeight: 700, margin: '0 0 20px' }}>概览</h1>

      <div style={{
        display: 'grid',
        gridTemplateColumns: 'repeat(auto-fill, minmax(200px, 1fr))',
        gap: 16,
        marginBottom: 24,
      }}>
        <StatCard title="钱包地址" value={config?.wallet_address ? `${config.wallet_address.slice(0, 8)}...${config.wallet_address.slice(-4)}` : '-'} />
        <StatCard title="模式" value={config?.trade_mode ?? '-'} />
        <StatCard title="基础代币" value={config?.base_token ?? '-'} />
        <StatCard title="跟单目标" value={String(config?.copy_targets?.length ?? 0)} />
      </div>

      <div style={{
        display: 'grid',
        gridTemplateColumns: 'repeat(auto-fill, minmax(200px, 1fr))',
        gap: 16,
        marginBottom: 24,
      }}>
        <StatCard title="今日交易" value={String(stats?.today?.total ?? 0)} subtitle={`成功: ${stats?.today?.success ?? 0}`} />
        <StatCard title="今日 PnL" value={`$${(stats?.today_pnl ?? 0).toFixed(2)}`} color={(stats?.today_pnl ?? 0) >= 0 ? 'var(--success)' : 'var(--danger)'} />
        <StatCard title="累计 PnL" value={`$${(stats?.all?.realized_pnl ?? 0).toFixed(2)}`} color={(stats?.all?.realized_pnl ?? 0) >= 0 ? 'var(--success)' : 'var(--danger)'} />
        <StatCard title="持仓数量" value={String(openCount)} />
        <StatCard title="累计投入" value={`$${(stats?.all?.total_invested ?? 0).toFixed(2)}`} />
      </div>

      {config?.copy_targets && config.copy_targets.length > 0 && (
        <div className="card" style={{ marginBottom: 16 }}>
          <div style={{ fontSize: 14, fontWeight: 600, marginBottom: 12 }}>跟单钱包列表</div>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
            <thead>
              <tr style={{ color: 'var(--text-secondary)', textAlign: 'left' }}>
                <th style={{ padding: '6px 8px' }}>地址</th>
                <th style={{ padding: '6px 8px' }}>备注</th>
                <th style={{ padding: '6px 8px' }}>模式</th>
              </tr>
            </thead>
            <tbody>
              {config.copy_targets.map((t) => (
                <tr key={t.address} style={{ borderTop: '1px solid var(--border)' }}>
                  <td style={{ padding: '8px', fontFamily: 'monospace', fontSize: 12 }}>{`${t.address.slice(0, 10)}...${t.address.slice(-6)}`}</td>
                  <td style={{ padding: '8px' }}>{t.remark || '-'}</td>
                  <td style={{ padding: '8px' }}>
                    <span style={{
                      display: 'inline-block',
                      padding: '2px 8px',
                      borderRadius: 6,
                      fontSize: 12,
                      background: t.trade_mode === 'monitor' ? 'var(--warning)' : 'var(--success)',
                      color: '#fff',
                      opacity: 0.85,
                    }}>
                      {t.trade_mode || config.trade_mode}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

function StatCard({ title, value, subtitle, color }: { title: string; value: string; subtitle?: string; color?: string }) {
  return (
    <div className="card">
      <div className="stat-label">{title}</div>
      <div className="stat-value" style={color ? { color } : undefined}>{value}</div>
      {subtitle && <div style={{ fontSize: 12, color: 'var(--text-muted)', marginTop: 4 }}>{subtitle}</div>}
    </div>
  )
}
