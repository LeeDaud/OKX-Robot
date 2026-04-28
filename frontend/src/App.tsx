import { BrowserRouter, Routes, Route } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Toaster } from "sonner";
import { ThemeProvider } from "@/components/theme-provider";
import Shell from "@/components/Shell";
import Dashboard from "@/pages/Dashboard";
import Wallets from "@/pages/Wallets";
import Params from "@/pages/Params";
import WalletPage from "@/pages/Wallet";
import Trades from "@/pages/Trades";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { refetchInterval: 10000, staleTime: 5000, retry: 1 },
  },
});

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <ThemeProvider>
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
      </ThemeProvider>
    </QueryClientProvider>
  );
}
