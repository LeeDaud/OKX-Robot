import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { fetchTrades, fetchTradeStats } from "@/lib/api";
import type { TradeStats, StrategyFilter } from "@/types/api";
import { PageHeader, SectionCard, MetricCard, LoadingState } from "@/components/app-primitives";
import { formatTime } from "@/lib/tokens";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { cn } from "@/lib/utils";

const strategyOptions: { value: StrategyFilter; label: string }[] = [
  { value: "all", label: "全部" },
  { value: "copy", label: "跟单" },
  { value: "grid", label: "网格" },
  { value: "dca", label: "DCA" },
  { value: "buyback", label: "回购卖" },
];

const strategyLabels: Record<string, string> = {
  "": "跟单",
  grid: "网格",
  grid_buy: "网格",
  grid_sell: "网格",
  dca: "DCA",
  deep_buy: "DCA",
  buyback_sell: "回购卖",
};

export default function Trades({ strategy: propStrategy }: { strategy?: StrategyFilter }) {
  const [strategyFilter, setStrategyFilter] = useState<StrategyFilter>(propStrategy || "all");

  // Use propStrategy if provided, otherwise use state
  const activeStrategy = propStrategy || strategyFilter;

  const { data: tradesData, isLoading } = useQuery({
    queryKey: ["trades", activeStrategy],
    queryFn: () => fetchTrades(100, 0, activeStrategy),
    refetchInterval: 60000, // 1分钟刷新一次
    staleTime: 30000,
  });
  const { data: stats } = useQuery<TradeStats>({
    queryKey: ["stats"],
    queryFn: fetchTradeStats,
    refetchInterval: 60000,
    staleTime: 30000,
  });

  const trades = tradesData?.trades ?? [];
  const todayPnl = stats?.today_pnl ?? 0;

  if (isLoading) return <LoadingState label="正在加载交易记录..." />;

  const pageTitle = propStrategy === "copy" ? "跟单交易记录" : propStrategy === "grid" ? "策略交易记录" : "交易记录";
  const pageDesc = propStrategy === "copy" ? "跟单交易的完整记录" : propStrategy === "grid" ? "网格策略交易的完整记录" : "跟单与策略交易的完整记录";

  return (
    <div className="space-y-6">
      <PageHeader title={pageTitle} description={pageDesc} />

      {stats && (
        <div className="grid gap-4 sm:grid-cols-3">
          <MetricCard label="今日交易" value={`${stats.today.total} 笔`} hint={`成功 ${stats.today.success} 笔`} />
          <MetricCard label="今日 PnL" value={`$${todayPnl.toFixed(2)}`} hint="今日盈亏" tone={todayPnl >= 0 ? "success" : "danger"} />
          <MetricCard label="累计交易" value={`${stats.all.total_trades} 笔`} hint={`总投入 $${stats.all.total_invested.toFixed(2)}`} />
        </div>
      )}

      <SectionCard title="交易明细" description={trades.length > 0 ? `最近 ${trades.length} 笔` : undefined}>
        {/* 策略筛选按钮组 - 仅在非固定策略页面显示 */}
        {!propStrategy && (
          <div className="mb-4 flex items-center gap-1.5">
            <span className="mr-1 text-[11px] font-semibold uppercase tracking-[0.16em] text-muted-foreground/60">
              策略
            </span>
            {strategyOptions.map((opt) => (
              <Button
                key={opt.value}
                variant={activeStrategy === opt.value ? "default" : "outline"}
                size="sm"
                onClick={() => setStrategyFilter(opt.value)}
                className={cn(
                  "text-xs",
                  activeStrategy === opt.value
                    ? ""
                    : "text-muted-foreground hover:text-foreground",
                )}
              >
                {opt.label}
              </Button>
            ))}
          </div>
        )}

        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>时间</TableHead>
              <TableHead>策略</TableHead>
              <TableHead>方向</TableHead>
              <TableHead>状态</TableHead>
              <TableHead>Token In</TableHead>
              <TableHead>Token Out</TableHead>
              <TableHead>PnL</TableHead>
              <TableHead>ROI</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {trades.length === 0 ? (
              <TableRow>
                <TableCell colSpan={8} className="py-8 text-center text-muted-foreground">暂无交易记录</TableCell>
              </TableRow>
            ) : (
              trades.map((t: any) => (
                <TableRow key={t.id}>
                  <TableCell className="whitespace-nowrap">{formatTime(t.created_at)}</TableCell>
                  <TableCell>
                    <span className="text-xs font-medium text-muted-foreground">
                      {strategyLabels[t.strategy] || t.strategy || "跟单"}
                    </span>
                  </TableCell>
                  <TableCell>
                    <Badge variant={t.side === "buy" ? "success" : "danger"}>{t.side === "buy" ? "买入" : "卖出"}</Badge>
                  </TableCell>
                  <TableCell>
                    <Badge variant={t.status === "success" ? "success" : t.status === "pending" ? "warning" : "danger"}>{t.status}</Badge>
                  </TableCell>
                  <TableCell className="font-mono text-xs">{t.token_in?.slice(0, 10) || "-"}...</TableCell>
                  <TableCell className="font-mono text-xs">{t.token_out?.slice(0, 10) || "-"}...</TableCell>
                  <TableCell className="font-semibold" style={{ color: (t.pnl || 0) >= 0 ? "var(--success)" : "var(--danger)" }}>
                    {t.pnl != null ? `$${t.pnl.toFixed(2)}` : "-"}
                  </TableCell>
                  <TableCell>{t.roi_pct != null ? `${(t.roi_pct * 100).toFixed(1)}%` : "-"}</TableCell>
                </TableRow>
              ))
            )}
          </TableBody>
        </Table>
      </SectionCard>
    </div>
  );
}
