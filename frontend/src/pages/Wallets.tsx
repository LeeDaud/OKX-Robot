import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Plus, Trash2 } from 'lucide-react'
import { fetchConfig, addTarget, deleteTarget } from '../lib/api'
import type { CopyTarget } from '../types/api'

export default function Wallets() {
  const qc = useQueryClient()
  const { data: config } = useQuery({ queryKey: ['config'], queryFn: fetchConfig })
  const [showForm, setShowForm] = useState(false)
  const [address, setAddress] = useState('')
  const [remark, setRemark] = useState('')
  const [mode, setMode] = useState('monitor')

  const addMut = useMutation({
    mutationFn: () => addTarget({ address, remark: remark || undefined, trade_mode: mode }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['config'] })
      setShowForm(false)
      setAddress('')
      setRemark('')
    },
  })

  const delMut = useMutation({
    mutationFn: (addr: string) => deleteTarget(addr),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['config'] }),
  })

  const targets = config?.copy_targets ?? []

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 20 }}>
        <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>跟单钱包</h1>
        <button
          onClick={() => setShowForm(!showForm)}
          style={{
            display: 'flex', alignItems: 'center', gap: 6,
            padding: '8px 16px', borderRadius: 10,
            background: 'var(--brand)', color: '#fff',
            border: 'none', cursor: 'pointer', fontSize: 14,
          }}
        >
          <Plus size={16} /> 添加钱包
        </button>
      </div>

      {showForm && (
        <div className="card" style={{ marginBottom: 16, display: 'flex', gap: 12, alignItems: 'flex-end' }}>
          <div style={{ flex: 1 }}>
            <label style={{ fontSize: 12, color: 'var(--text-secondary)', display: 'block', marginBottom: 4 }}>地址</label>
            <input
              value={address} onChange={(e) => setAddress(e.target.value)}
              placeholder="0x..."
              style={{ width: '100%', padding: '8px 10px', borderRadius: 8, border: '1px solid var(--border)', fontSize: 13, fontFamily: 'monospace', boxSizing: 'border-box' }}
            />
          </div>
          <div style={{ flex: 0.4 }}>
            <label style={{ fontSize: 12, color: 'var(--text-secondary)', display: 'block', marginBottom: 4 }}>备注</label>
            <input
              value={remark} onChange={(e) => setRemark(e.target.value)}
              placeholder="EFO"
              style={{ width: '100%', padding: '8px 10px', borderRadius: 8, border: '1px solid var(--border)', fontSize: 13, boxSizing: 'border-box' }}
            />
          </div>
          <div style={{ flex: 0.3 }}>
            <label style={{ fontSize: 12, color: 'var(--text-secondary)', display: 'block', marginBottom: 4 }}>模式</label>
            <select
              value={mode} onChange={(e) => setMode(e.target.value)}
              style={{ width: '100%', padding: '8px 10px', borderRadius: 8, border: '1px solid var(--border)', fontSize: 13, boxSizing: 'border-box' }}
            >
              <option value="monitor">监测</option>
              <option value="ratio">比例跟单</option>
              <option value="fixed">固定跟单</option>
            </select>
          </div>
          <button
            onClick={() => addMut.mutate()}
            disabled={!address || addMut.isPending}
            style={{
              padding: '8px 20px', borderRadius: 8,
              background: !address ? 'var(--text-muted)' : 'var(--brand)',
              color: '#fff', border: 'none', cursor: 'pointer', fontSize: 13, whiteSpace: 'nowrap',
            }}
          >
            添加
          </button>
        </div>
      )}

      <div className="card">
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
          <thead>
            <tr style={{ color: 'var(--text-secondary)', textAlign: 'left' }}>
              <th style={{ padding: '8px 10px' }}>地址</th>
              <th style={{ padding: '8px 10px' }}>备注</th>
              <th style={{ padding: '8px 10px' }}>模式</th>
              <th style={{ padding: '8px 10px' }}>比例</th>
              <th style={{ padding: '8px 10px' }}>固定金额</th>
              <th style={{ padding: '8px 10px' }}>操作</th>
            </tr>
          </thead>
          <tbody>
            {targets.length === 0 && (
              <tr><td colSpan={6} style={{ padding: 20, textAlign: 'center', color: 'var(--text-muted)' }}>暂无跟单钱包</td></tr>
            )}
            {targets.map((t: CopyTarget) => (
              <tr key={t.address} style={{ borderTop: '1px solid var(--border)' }}>
                <td style={{ padding: '10px', fontFamily: 'monospace', fontSize: 12 }}>{`${t.address.slice(0, 12)}...${t.address.slice(-6)}`}</td>
                <td style={{ padding: '10px' }}>{t.remark || '-'}</td>
                <td style={{ padding: '10px' }}>
                  <span style={{
                    display: 'inline-block', padding: '2px 8px', borderRadius: 6, fontSize: 12,
                    background: t.trade_mode === 'monitor' ? 'var(--warning)' : 'var(--success)', color: '#fff', opacity: 0.85,
                  }}>
                    {t.trade_mode || config?.trade_mode || '-'}
                  </span>
                </td>
                <td style={{ padding: '10px' }}>{t.trade_ratio != null ? `${(t.trade_ratio * 100).toFixed(0)}%` : '-'}</td>
                <td style={{ padding: '10px' }}>{t.trade_fixed_usd ? `$${t.trade_fixed_usd}` : '-'}</td>
                <td style={{ padding: '10px' }}>
                  <button
                    onClick={() => { if (confirm('确认删除此目标？')) delMut.mutate(t.address) }}
                    style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--danger)', padding: 4 }}
                  >
                    <Trash2 size={16} />
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
