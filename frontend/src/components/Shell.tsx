import { NavLink, Outlet } from 'react-router-dom'
import { LayoutDashboard, Wallet, SlidersHorizontal, UserCircle, History } from 'lucide-react'

const navItems = [
  { to: '/', label: '概览', icon: LayoutDashboard },
  { to: '/wallets', label: '跟单钱包', icon: Wallet },
  { to: '/params', label: '跟单参数', icon: SlidersHorizontal },
  { to: '/wallet', label: '执行钱包', icon: UserCircle },
  { to: '/trades', label: '交易记录', icon: History },
]

export default function Shell() {
  return (
    <div style={{ display: 'flex', minHeight: '100vh' }}>
      {/* Sidebar */}
      <aside
        style={{
          width: 220,
          background: 'var(--sidebar)',
          borderRight: '1px solid var(--border)',
          padding: '20px 12px',
          display: 'flex',
          flexDirection: 'column',
          flexShrink: 0,
        }}
      >
        <div style={{ padding: '0 10px 20px', fontSize: 16, fontWeight: 700, color: 'var(--brand)' }}>
          OKX Robot
        </div>
        <nav style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
          {navItems.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.to === '/'}
              className="sidebar-link"
            >
              <item.icon size={18} />
              {item.label}
            </NavLink>
          ))}
        </nav>
      </aside>

      {/* Main */}
      <main style={{ flex: 1, padding: 24, minWidth: 0 }}>
        <Outlet />
      </main>
    </div>
  )
}
