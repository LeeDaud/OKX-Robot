import { BrowserRouter, Routes, Route } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Toaster } from "sonner";
import { lazy, Suspense } from "react";
import { ThemeProvider } from "@/components/theme-provider";
import { ErrorBoundary } from "@/components/ErrorBoundary";
import Shell from "@/components/Shell";
import Dashboard from "@/pages/Dashboard";
import { LoadingState } from "@/components/app-primitives";

const Wallets = lazy(() => import("@/pages/Wallets"));
const Params = lazy(() => import("@/pages/Params"));
const WalletPage = lazy(() => import("@/pages/Wallet"));
const Trades = lazy(() => import("@/pages/Trades"));
const Positions = lazy(() => import("@/pages/Positions"));
const Grid = lazy(() => import("@/pages/Grid"));
const AeroTrend = lazy(() => import("@/pages/AeroTrend"));

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
              <Route path="/wallets" element={<Suspense fallback={<LoadingState label="加载中..." />}><Wallets /></Suspense>} />
              <Route path="/params" element={<Suspense fallback={<LoadingState label="加载中..." />}><Params /></Suspense>} />
              <Route path="/wallet" element={<Suspense fallback={<LoadingState label="加载中..." />}><WalletPage /></Suspense>} />
              <Route path="/grid" element={<Suspense fallback={<LoadingState label="加载中..." />}><Grid /></Suspense>} />
              <Route path="/aero" element={<Suspense fallback={<LoadingState label="加载中..." />}><AeroTrend /></Suspense>} />
              <Route path="/positions" element={<Suspense fallback={<LoadingState label="加载中..." />}><Positions /></Suspense>} />
              <Route path="/positions/copy" element={<Suspense fallback={<LoadingState label="加载中..." />}><Positions strategy="copy" /></Suspense>} />
              <Route path="/positions/grid" element={<Suspense fallback={<LoadingState label="加载中..." />}><Positions strategy="grid" /></Suspense>} />
              <Route path="/positions/aero" element={<Suspense fallback={<LoadingState label="加载中..." />}><Positions strategy="aero_trend" /></Suspense>} />
              <Route path="/trades" element={<Suspense fallback={<LoadingState label="加载中..." />}><Trades /></Suspense>} />
              <Route path="/trades/copy" element={<Suspense fallback={<LoadingState label="加载中..." />}><Trades strategy="copy" /></Suspense>} />
              <Route path="/trades/grid" element={<Suspense fallback={<LoadingState label="加载中..." />}><Trades strategy="grid" /></Suspense>} />
              <Route path="/trades/aero" element={<Suspense fallback={<LoadingState label="加载中..." />}><Trades strategy="aero_trend" /></Suspense>} />
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
