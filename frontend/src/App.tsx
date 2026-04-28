import { BrowserRouter, Routes, Route } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import Shell from './components/Shell'
import Dashboard from './pages/Dashboard'
import Wallets from './pages/Wallets'
import Params from './pages/Params'
import WalletPage from './pages/Wallet'
import Trades from './pages/Trades'

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { refetchInterval: 10000, staleTime: 5000, retry: 1 },
  },
})

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <Routes>
          <Route element={<Shell />}>
            <Route path="/" element={<Dashboard />} />
            <Route path="/wallets" element={<Wallets />} />
            <Route path="/params" element={<Params />} />
            <Route path="/wallet" element={<WalletPage />} />
            <Route path="/trades" element={<Trades />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </QueryClientProvider>
  )
}
