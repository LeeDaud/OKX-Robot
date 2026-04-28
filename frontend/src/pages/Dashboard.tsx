import { useQuery } from "@tanstack/react-query";
import { fetchConfig, fetchTradeStats, fetchPositions, fetchBalances } from "@/lib/api";
import type { AppConfig, TradeStats, BalancesResponse } from "@/types/api";
import { PageHeader, MetricCard, SectionCard, LoadingState } from "@/components/app-primitives";
import { Badge } from "@/components/ui/badge";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";

export default function Dashboard() {
  const { data: config, isLoading } = useQuery<AppConfig>({ queryKey: ["config"], queryFn: fetchConfig });
  const { data: stats } = useQuery<TradeStats>({ queryKey: ["stats"], queryFn: fetchTradeStats });
  const { data: positions } = useQuery({ queryKey: ["positions"], queryFn: fetchPositions });
  const { data: balanceData } = useQuery<BalancesResponse>({ queryKey: ["balances"], queryFn: fetchBalances });

  if (isLoading) return <LoadingState label="正在加载概览..." />;

  const openCount = positions?.positions?.length ?? 0;
  const todayPnl = stats?.today_pnl ?? 0;
  const baseToken = config?.base_token ?? "USDC";
  const baseBalance = balanceData?.balances?.[baseToken];
  const ethBalance = balanceData?.balances?.ETH;

  return (
    <div className="space-y-6">
      <PageHeader title="概览" description="跟单机器人运行状态一览" />

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <MetricCard label="跟单目标" value={String(config?.copy_targets?.length ?? 0)} hint="已配置的跟单钱包数量" />
        <MetricCard label="今日交易" value={String(stats?.today?.total ?? 0)} hint={`成功 ${stats?.today?.success ?? 0} 笔`} />
        <MetricCard label="今日 PnL" value={`$${todayPnl.toFixed(2)}`} hint="今日盈亏" tone={todayPnl >= 0 ? "success" : "danger"} />
        <MetricCard label="持仓数量" value={String(openCount)} hint="当前持有仓位" />
      </div>

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {baseBalance != null ? (
          <MetricCard label={`${baseToken} 余额`} value={`${baseBalance.toFixed(4)}`} hint={`基础交易代币`} />
        ) : (
          <MetricCard label={`${baseToken} 余额`} value="-" hint="查询失败或余额为空" />
        )}
        {ethBalance != null ? (
          <MetricCard label="ETH 余额" value={`${ethBalance.toFixed(4)}`} hint={`Gas 代币`} />
        ) : (
          <MetricCard label="ETH 余额" value="-" hint="查询失败" />
        )}
        <MetricCard label="累计 PnL" value={`$${(stats?.all?.realized_pnl ?? 0).toFixed(2)}`} hint="已实现盈亏" tone={(stats?.all?.realized_pnl ?? 0) >= 0 ? "success" : "danger"} />
        <MetricCard label="运行模式" value={config?.dry_run ? "Dry Run" : "Live"} hint={`${baseToken} · ${config?.trade_mode ?? "-"}`} tone={config?.dry_run ? "warning" : "success"} />
      </div>

      {config?.copy_targets && config.copy_targets.length > 0 && (
        <SectionCard title="跟单钱包列表" description={`共 ${config.copy_targets.length} 个目标地址`}>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>地址</TableHead>
                <TableHead>备注</TableHead>
                <TableHead>模式</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {config.copy_targets.map((t) => (
                <TableRow key={t.address}>
                  <TableCell className="font-mono text-xs">{t.address.slice(0, 10)}...{t.address.slice(-6)}</TableCell>
                  <TableCell>{t.remark || "-"}</TableCell>
                  <TableCell>
                    <Badge variant={t.trade_mode === "monitor" ? "warning" : "success"}>
                      {t.trade_mode || config.trade_mode}
                    </Badge>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </SectionCard>
      )}
    </div>
  );
}
