import { useQuery } from '@tanstack/react-query'
import { fetchWallet, fetchConfig } from '../lib/api'

export default function WalletPage() {
  const { data: wallet } = useQuery({ queryKey: ['wallet'], queryFn: fetchWallet })
  const { data: config } = useQuery({ queryKey: ['config'], queryFn: fetchConfig })

  return (
    <div>
      <h1 style={{ fontSize: 22, fontWeight: 700, margin: '0 0 20px' }}>执行钱包</h1>

      <div className="card" style={{ marginBottom: 16 }}>
        <h2 style={{ fontSize: 15, fontWeight: 600, margin: '0 0 16px' }}>钱包信息</h2>
        <div style={{ fontSize: 13, lineHeight: 2 }}>
          <div><strong>地址：</strong> <span style={{ fontFamily: 'monospace', fontSize: 12 }}>{wallet?.wallet_address || '-'}</span></div>
          <div><strong>RPC：</strong> <span style={{ fontSize: 12 }}>{wallet?.rpc_http_url || '-'}</span></div>
          <div><strong>运行模式：</strong> {config?.dry_run ? <span style={{ color: 'var(--warning)' }}>Dry Run</span> : <span style={{ color: 'var(--success)' }}>Live</span>}</div>
        </div>
      </div>

      <div className="card">
        <h2 style={{ fontSize: 15, fontWeight: 600, margin: '0 0 16px' }}>风险提示</h2>
        <ul style={{ fontSize: 13, color: 'var(--text-secondary)', lineHeight: 1.8, margin: 0, paddingLeft: 20 }}>
          <li>私钥和 API Key 仅存储在服务器 .env 文件中，前端不展示</li>
          <li>请勿将此管理页面暴露到公网</li>
          <li>日亏损上限当前设置为 ${config?.daily_loss_limit_usd ?? '-'}</li>
        </ul>
      </div>
    </div>
  )
}
