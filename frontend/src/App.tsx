import { BrowserRouter, Routes, Route } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Toaster } from "sonner";
import { ThemeProvider } from "@/components/theme-provider";
import { ErrorBoundary } from "@/components/ErrorBoundary";
import Shell from "@/components/Shell";
import Dashboard from "@/pages/Dashboard";
import Wallets from "@/pages/Wallets";
import Params from "@/pages/Params";
import WalletPage from "@/pages/Wallet";
import Trades from "@/pages/Trades";
import Positions from "@/pages/Positions";
import Grid from "@/pages/Grid";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      refetchInterval: false, // 默认不自动刷新
      staleTime: 30000, // 30秒内认为数据新鲜
      retry: 1,
      refetchOnWindowFocus: false, // 窗口聚焦时不刷新
    },
  },
});

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <ThemeProvider>
        <ErrorBoundary>
        <BrowserRouter>
          <Routes>
            <Route element={<Shell />}>
              <Route path="/" element={<Dashboard />} />
              <Route path="/wallets" element={<Wallets />} />
              <Route path="/params" element={<Params />} />
              <Route path="/wallet" element={<WalletPage />} />
              <Route path="/grid" element={<Grid />} />
              <Route path="/positions" element={<Positions />} />
              <Route path="/positions/copy" element={<Positions strategy="copy" />} />
              <Route path="/positions/grid" element={<Positions strategy="grid" />} />
              <Route path="/trades" element={<Trades />} />
              <Route path="/trades/copy" element={<Trades strategy="copy" />} />
              <Route path="/trades/grid" element={<Trades strategy="grid" />} />
            </Route>
          </Routes>
        </BrowserRouter>
        <Toaster
          position="top-right"
          richColors
          closeButton
          toastOptions={{
            style: {
              background: "var(--popover-elevated)",
              border: "1px solid var(--border-strong)",
              color: "var(--foreground)",
              boxShadow: "var(--shadow-soft)",
              backdropFilter: "blur(18px)",
            },
          }}
        />
        </ErrorBoundary>
      </ThemeProvider>
    </QueryClientProvider>
  );
}
