import { useQuery } from "@tanstack/react-query";
import { fetchTrades, fetchTradeStats } from "@/lib/api";
import type { TradeStats } from "@/types/api";
import { PageHeader, SectionCard, MetricCard, LoadingState } from "@/components/app-primitives";
import { Badge } from "@/components/ui/badge";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";

export default function Trades() {
  const { data: tradesData, isLoading } = useQuery({
    queryKey: ["trades"],
    queryFn: () => fetchTrades(100, 0),
  });
  const { data: stats } = useQuery<TradeStats>({ queryKey: ["stats"], queryFn: fetchTradeStats });

  const trades = tradesData?.trades ?? [];
  const todayPnl = stats?.today_pnl ?? 0;

  if (isLoading) return <LoadingState label="正在加载交易记录..." />;

  return (
    <div className="space-y-6">
      <PageHeader title="交易记录" description="跟单交易的完整记录" />

      {stats && (
        <div className="grid gap-4 sm:grid-cols-3">
          <MetricCard label="今日交易" value={`${stats.today.total} 笔`} hint={`成功 ${stats.today.success} 笔`} />
          <MetricCard label="今日 PnL" value={`$${todayPnl.toFixed(2)}`} hint="今日盈亏" tone={todayPnl >= 0 ? "success" : "danger"} />
          <MetricCard label="累计交易" value={`${stats.all.total_trades} 笔`} hint={`总投入 $${stats.all.total_invested.toFixed(2)}`} />
        </div>
      )}

      <SectionCard title="交易明细" description={trades.length > 0 ? `最近 ${trades.length} 笔` : undefined}>
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>时间</TableHead>
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
                <TableCell colSpan={7} className="text-center py-8 text-muted-foreground">暂无交易记录</TableCell>
              </TableRow>
            ) : (
              trades.map((t: any) => (
                <TableRow key={t.id}>
                  <TableCell className="whitespace-nowrap">{t.created_at?.slice(11, 19) || "-"}</TableCell>
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
