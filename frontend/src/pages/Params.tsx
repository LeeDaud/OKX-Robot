import { useState, useEffect } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { fetchConfig, updateParams } from '../lib/api'

export default function Params() {
  const qc = useQueryClient()
  const { data: config } = useQuery({ queryKey: ['config'], queryFn: fetchConfig })
  const [form, setForm] = useState<Record<string, any>>({})
  const [saving, setSaving] = useState(false)
  const [msg, setMsg] = useState('')

  useEffect(() => {
    if (config) {
      setForm({
        base_token: config.base_token,
        trade_mode: config.trade_mode,
        trade_ratio: config.trade_ratio,
        trade_fixed_usd: config.trade_fixed_usd,
        trade_max_usd: config.trade_max_usd,
        trade_fixed_virtuals: config.trade_fixed_virtuals,
        slippage: config.slippage,
        gas_limit_gwei: config.gas_limit_gwei,
        daily_loss_limit_usd: config.daily_loss_limit_usd,
        take_profit_roi: config.take_profit_roi,
        take_profit_check_sec: config.take_profit_check_sec,
        poll_interval_sec: config.poll_interval_sec,
        min_trade_usd: config.min_trade_usd,
        dry_run: config.dry_run,
      })
    }
  }, [config])

  const handleSave = async () => {
    setSaving(true)
    setMsg('')
    try {
      await updateParams(form)
      qc.invalidateQueries({ queryKey: ['config'] })
      setMsg('保存成功')
    } catch (e: any) {
      setMsg(`保存失败: ${e.message}`)
    }
    setSaving(false)
    setTimeout(() => setMsg(''), 3000)
  }

  if (!config) return <div style={{ color: 'var(--text-muted)' }}>加载中...</div>

  return (
    <div>
      <h1 style={{ fontSize: 22, fontWeight: 700, margin: '0 0 20px' }}>跟单参数</h1>

      <div className="card" style={{ marginBottom: 16 }}>
        <h2 style={{ fontSize: 15, fontWeight: 600, margin: '0 0 16px' }}>全局参数</h2>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
          <Field label="基础代币" value={form.base_token} onChange={(v) => setForm({ ...form, base_token: v })} type="select" options={['VIRTUAL', 'USDC']} />
          <Field label="跟单模式" value={form.trade_mode} onChange={(v) => setForm({ ...form, trade_mode: v })} type="select" options={['ratio', 'fixed', 'monitor']} />
          <Field label="跟单比例" value={form.trade_ratio} onChange={(v) => setForm({ ...form, trade_ratio: parseFloat(v) || 0 })} type="number" step="0.01" />
          <Field label="固定金额(USDC)" value={form.trade_fixed_usd} onChange={(v) => setForm({ ...form, trade_fixed_usd: parseFloat(v) || 0 })} type="number" />
          <Field label="单笔上限(USDC)" value={form.trade_max_usd} onChange={(v) => setForm({ ...form, trade_max_usd: parseFloat(v) || 0 })} type="number" />
          <Field label="固定 VIRTUAL 数量" value={form.trade_fixed_virtuals} onChange={(v) => setForm({ ...form, trade_fixed_virtuals: parseFloat(v) || 0 })} type="number" />
          <Field label="最大滑点" value={form.slippage} onChange={(v) => setForm({ ...form, slippage: parseFloat(v) || 0 })} type="number" step="0.001" />
          <Field label="Gas 上限(gwei)" value={form.gas_limit_gwei} onChange={(v) => setForm({ ...form, gas_limit_gwei: parseFloat(v) || 0 })} type="number" />
          <Field label="日亏损上限(USDC)" value={form.daily_loss_limit_usd} onChange={(v) => setForm({ ...form, daily_loss_limit_usd: parseFloat(v) || 0 })} type="number" />
          <Field label="止盈 ROI" value={form.take_profit_roi} onChange={(v) => setForm({ ...form, take_profit_roi: parseFloat(v) || 0 })} type="number" step="0.01" />
          <Field label="轮询间隔(秒)" value={form.poll_interval_sec} onChange={(v) => setForm({ ...form, poll_interval_sec: parseFloat(v) || 0 })} type="number" />
          <Field label="最小跟单额(USDC)" value={form.min_trade_usd} onChange={(v) => setForm({ ...form, min_trade_usd: parseFloat(v) || 0 })} type="number" />
          <Field label="Dry Run" value={form.dry_run} onChange={(v) => setForm({ ...form, dry_run: v === 'true' })} type="select" options={['false', 'true']} />
        </div>
        <div style={{ marginTop: 20, display: 'flex', alignItems: 'center', gap: 12 }}>
          <button onClick={handleSave} disabled={saving} style={{
            padding: '10px 28px', borderRadius: 10,
            background: 'var(--brand)', color: '#fff',
            border: 'none', cursor: 'pointer', fontSize: 14,
          }}>
            {saving ? '保存中...' : '保存'}
          </button>
          {msg && <span style={{ fontSize: 13, color: msg.includes('成功') ? 'var(--success)' : 'var(--danger)' }}>{msg}</span>}
        </div>
      </div>

      <div className="card">
        <h2 style={{ fontSize: 15, fontWeight: 600, margin: '0 0 12px' }}>说明</h2>
        <ul style={{ fontSize: 13, color: 'var(--text-secondary)', lineHeight: 1.8, margin: 0, paddingLeft: 20 }}>
          <li>修改参数后 bot 在 60 秒内自动生效（热加载）</li>
          <li>各钱包可独立覆盖全局参数（需在 config.yaml 中逐钱包配置）</li>
          <li>monitor 模式仅监测，不执行跟单</li>
        </ul>
      </div>
    </div>
  )
}

function Field({ label, value, onChange, type = 'text', step, options }: {
  label: string; value: any; onChange: (v: string) => void; type?: string; step?: string; options?: string[]
}) {
  return (
    <div>
      <label style={{ fontSize: 12, color: 'var(--text-secondary)', display: 'block', marginBottom: 4 }}>{label}</label>
      {type === 'select' && options ? (
        <select value={String(value)} onChange={(e) => onChange(e.target.value)} style={{
          width: '100%', padding: '8px 10px', borderRadius: 8, border: '1px solid var(--border)', fontSize: 13, boxSizing: 'border-box',
        }}>
          {options.map((o) => <option key={o} value={o}>{o}</option>)}
        </select>
      ) : (
        <input type={type} value={value ?? ''} onChange={(e) => onChange(e.target.value)} step={step} style={{
          width: '100%', padding: '8px 10px', borderRadius: 8, border: '1px solid var(--border)', fontSize: 13, boxSizing: 'border-box',
        }} />
      )}
    </div>
  )
}
