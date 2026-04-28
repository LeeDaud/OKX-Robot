import { useState } from "react";
import { NavLink, Outlet, useLocation } from "react-router-dom";
import {
  LayoutDashboard,
  Wallet,
  SlidersHorizontal,
  UserCircle,
  History,
  Package,
  MoonStar,
  SunMedium,
  RefreshCcw,
  Menu,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Sheet, SheetContent } from "@/components/ui/dialog";
import { useTheme } from "@/components/theme-provider";
import { useQueryClient } from "@tanstack/react-query";

const navItems = [
  { to: "/", label: "运营概览", icon: LayoutDashboard },
  { to: "/wallets", label: "跟单钱包", icon: Wallet },
  { to: "/params", label: "跟单参数", icon: SlidersHorizontal },
  { to: "/wallet", label: "执行钱包", icon: UserCircle },
  { to: "/positions", label: "实时持仓", icon: Package },
  { to: "/trades", label: "交易记录", icon: History },
];

function BrandBlock({ compact = false }: { compact?: boolean }) {
  return (
    <div className={`flex items-center gap-4 ${compact ? "justify-center" : ""}`}>
      <div className="theme-brand-badge flex size-12 shrink-0 items-center justify-center rounded-[18px]">
        <div className="flex size-8 items-center justify-center rounded-[10px] bg-primary text-xs font-bold text-primary-foreground">
          AT
        </div>
      </div>
      {compact ? null : (
        <div className="min-w-0">
          <div className="text-[11px] font-semibold uppercase tracking-[0.22em] text-primary/80">
            Auto Trader
          </div>
          <div className="text-xl font-semibold tracking-[-0.04em]">管理面板</div>
        </div>
      )}
    </div>
  );
}

function NavList({ compact = false, onNavigate }: { compact?: boolean; onNavigate?: () => void }) {
  return (
    <div className="space-y-2">
      {navItems.map((item) => {
        const Icon = item.icon;
        return (
          <NavLink
            key={item.to}
            to={item.to}
            end={item.to === "/"}
            title={item.label}
            onClick={onNavigate}
            className={({ isActive }) =>
              `group flex items-center rounded-[22px] text-sm font-medium transition ${
                compact ? "justify-center px-3 py-3" : "justify-between px-4 py-3"
              } ${
                isActive
                  ? "bg-[color:var(--surface-soft-strong)] text-foreground shadow-sm"
                  : "text-muted-foreground hover:bg-[color:var(--surface-soft)] hover:text-foreground"
              }`
            }
          >
            <span className={`flex items-center gap-3 ${compact ? "justify-center" : ""}`}>
              <Icon className="size-4 shrink-0" />
              {compact ? null : item.label}
            </span>
          </NavLink>
        );
      })}
    </div>
  );
}

function SidebarPanel({ mode }: { mode: "expanded" | "rail" }) {
  return (
    <aside
      className={`flex h-full flex-col gap-6 rounded-[30px] border border-white/50 surface-panel shadow-[var(--shadow-strong)] ${
        mode === "expanded" ? "p-5" : "items-center p-3"
      }`}
    >
      <BrandBlock compact={mode === "rail"} />
      <div className="w-full">
        <NavList compact={mode === "rail"} />
      </div>
    </aside>
  );
}

function TopBar({ onCycleSidebar, onOpenMobile }: { onCycleSidebar: () => void; onOpenMobile: () => void }) {
  const { theme, toggleTheme } = useTheme();
  const queryClient = useQueryClient();

  return (
    <Card className="surface-glass sticky top-4 z-20 rounded-[28px] p-4">
      <div className="flex items-center justify-between gap-4">
        <div className="flex items-center gap-3">
          <Button variant="secondary" size="icon" className="hidden lg:inline-flex" onClick={onCycleSidebar}>
            <Menu className="size-4" />
          </Button>
          <Button variant="secondary" size="icon" className="lg:hidden" onClick={onOpenMobile}>
            <Menu className="size-4" />
          </Button>
          <div className="theme-brand-badge flex size-11 shrink-0 items-center justify-center rounded-[16px]">
            <div className="flex size-7 items-center justify-center rounded-[9px] bg-primary text-[10px] font-bold text-primary-foreground">
              AT
            </div>
          </div>
          <div className="hidden min-w-0 sm:block">
            <div className="text-[11px] font-semibold uppercase tracking-[0.2em] text-primary/80">Auto Trader</div>
            <div className="text-sm font-semibold tracking-[-0.03em]">管理面板</div>
          </div>
        </div>

        <div className="flex items-center gap-2">
          <Button variant="outline" className="theme-toggle-button" onClick={toggleTheme}>
            {theme === "light" ? <MoonStar className="size-4" /> : <SunMedium className="size-4" />}
            {theme === "light" ? "深色模式" : "浅色模式"}
          </Button>
          <Button variant="outline" onClick={() => queryClient.invalidateQueries()}>
            <RefreshCcw className="size-4" />
            刷新
          </Button>
        </div>
      </div>
    </Card>
  );
}

export default function Shell() {
  const [sidebarMode, setSidebarMode] = useState<"expanded" | "rail">("expanded");
  const [mobileOpen, setMobileOpen] = useState(false);
  const location = useLocation();

  return (
    <div className="min-h-screen px-4 py-4 lg:px-6 lg:py-6">
      <div
        className={`mx-auto grid max-w-[1800px] gap-5 ${
          sidebarMode === "expanded" ? "lg:grid-cols-[300px_minmax(0,1fr)]" : "lg:grid-cols-[96px_minmax(0,1fr)]"
        }`}
      >
        <div className="hidden lg:block">
          <SidebarPanel mode={sidebarMode} />
        </div>

        <div className="flex min-h-[calc(100vh-2rem)] flex-col gap-5">
          <TopBar
            onCycleSidebar={() => setSidebarMode((prev) => (prev === "expanded" ? "rail" : "expanded"))}
            onOpenMobile={() => setMobileOpen(true)}
          />
          <main className="flex-1">
            <div key={location.pathname} className="space-y-6 animate-in fade-in-0 slide-in-from-bottom-1 duration-300">
              <Outlet />
            </div>
          </main>
        </div>
      </div>

      <Sheet open={mobileOpen} onOpenChange={setMobileOpen}>
        <SheetContent className="overflow-y-auto bg-sidebar/95">
          <div className="space-y-6 pt-12">
            <BrandBlock />
            <NavList onNavigate={() => setMobileOpen(false)} />
          </div>
        </SheetContent>
      </Sheet>
    </div>
  );
}
