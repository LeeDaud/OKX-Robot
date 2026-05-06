import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { fetchConfig, fetchTradeStats, fetchPositions, fetchBalances, fetchGridState, fetchWallet as fetchWalletApi, toggleExecution } from "@/lib/api";
import type { AppConfig, TradeStats, BalancesResponse, GridState, WalletInfo } from "@/types/api";
import { PageHeader, MetricCard, SectionCard, LoadingState } from "@/components/app-primitives";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Card, CardContent } from "@/components/ui/card";
import { Settings2, Power, PowerOff } from "lucide-react";
import { cn } from "@/lib/utils";
import { shortenAddress } from "@/lib/tokens";
import { toast } from "sonner";

export default function Dashboard() {
  const qc = useQueryClient();
  const { data: config, isLoading } = useQuery<AppConfig>({ queryKey: ["config"], queryFn: fetchConfig });
  const { data: stats } = useQuery<TradeStats>({ queryKey: ["stats"], queryFn: fetchTradeStats });
  const { data: positions } = useQuery({ queryKey: ["positions"], queryFn: fetchPositions });
  const { data: balanceData } = useQuery<BalancesResponse>({ queryKey: ["balances"], queryFn: fetchBalances });
  const { data: gridState } = useQuery<GridState>({ queryKey: ["grid-state"], queryFn: fetchGridState });
  const { data: wallet } = useQuery<WalletInfo>({ queryKey: ["wallet"], queryFn: fetchWalletApi });

  if (isLoading) return <LoadingState label="正在加载概览..." />;

  const openCount = positions?.positions?.length ?? 0;
  const todayPnl = stats?.today_pnl ?? 0;
  const baseToken = config?.base_token ?? "USDC";
  const baseBalance = balanceData?.balances?.[baseToken];
  const ethBalance = balanceData?.balances?.ETH;

  const gridEnabled = gridState?.enabled ?? false;
  const gridActiveSlots = gridState?.slots?.filter((s) => s.status === "bought").length ?? 0;
  const copyEnabled = config?.copy_trading?.enabled ?? false;
  const isWalletReady = wallet?.has_private_key && wallet?.has_okx_api_key;

  const toggleMut = useMutation({
    mutationFn: toggleExecution,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["config"] });
      qc.invalidateQueries({ queryKey: ["grid-state"] });
      toast.success("配置已更新");
    },
    onError: (e: Error) => toast.error(`更新失败: ${e.message}`),
  });

  return (
    <div className="space-y-8">
      <PageHeader eyebrow="overview" title="运营概览" description="钱包、跟单与策略运行状态总览" />

      {/* ── 执行控制 ── */}
      <section>
        <div className="mb-4 flex items-center gap-3">
          <div className="h-px flex-1 bg-border/40" />
          <span className="text-[11px] font-semibold uppercase tracking-[0.22em] text-muted-foreground/60">
            执行控制
          </span>
          <div className="h-px flex-1 bg-border/40" />
        </div>
        <Card>
          <CardContent className="p-5">
            <div className="grid gap-4 sm:grid-cols-3">
              <div className="flex flex-col gap-2">
                <span className="text-xs font-semibold uppercase tracking-[0.12em] text-muted-foreground">跟单交易</span>
                <Button
                  variant={copyEnabled ? "default" : "outline"}
                  size="sm"
                  disabled={toggleMut.isPending}
                  onClick={() => toggleMut.mutate({ copy_trading_enabled: !copyEnabled })}
                  className="justify-start"
                >
                  {copyEnabled ? <Power className="size-4" /> : <PowerOff className="size-4" />}
                  {copyEnabled ? "运行中" : "已停止"}
                </Button>
              </div>
              <div className="flex flex-col gap-2">
                <span className="text-xs font-semibold uppercase tracking-[0.12em] text-muted-foreground">网格策略</span>
                <Button
                  variant={gridEnabled ? "default" : "outline"}
                  size="sm"
                  disabled={toggleMut.isPending}
                  onClick={() => toggleMut.mutate({ grid_enabled: !gridEnabled })}
                  className="justify-start"
                >
                  {gridEnabled ? <Power className="size-4" /> : <PowerOff className="size-4" />}
                  {gridEnabled ? "运行中" : "已停止"}
                </Button>
              </div>
              <div className="flex flex-col gap-2">
                <span className="text-xs font-semibold uppercase tracking-[0.12em] text-muted-foreground">运行模式</span>
                <Button
                  variant={config?.dry_run ? "outline" : "default"}
                  size="sm"
                  disabled={toggleMut.isPending}
                  onClick={() => toggleMut.mutate({ dry_run: !config?.dry_run })}
                  className="justify-start"
                >
                  <Badge variant={config?.dry_run ? "warning" : "success"} className="mr-2">
                    {config?.dry_run ? "Dry Run" : "Live"}
                  </Badge>
                  {config?.dry_run ? "切换至实盘" : "切换至模拟"}
                </Button>
              </div>
            </div>
          </CardContent>
        </Card>
      </section>

      {/* ── 钱包状态 ── */}
      <section>
        <div className="mb-4 flex items-center gap-3">
          <div className="h-px flex-1 bg-border/40" />
          <span className="text-[11px] font-semibold uppercase tracking-[0.22em] text-muted-foreground/60">
            执行钱包
          </span>
          <div className="h-px flex-1 bg-border/40" />
        </div>
        <Card>
          <CardContent className="p-5">
            <div className="flex flex-col gap-5 sm:flex-row sm:items-center sm:justify-between">
              {/* 左侧：地址 + 状态 */}
              <div className="space-y-3">
                <div className="flex items-center gap-3">
                  <span className="font-mono text-sm">
                    {wallet?.wallet_address ? shortenAddress(wallet.wallet_address, 10, 6) : "-"}
                  </span>
                  <Badge variant={config?.dry_run ? "warning" : "success"}>
                    {config?.dry_run ? "Dry Run" : "Live"}
                  </Badge>
                </div>
                <div className="flex flex-wrap gap-4 text-xs text-muted-foreground">
                  <span>私钥: <strong className={wallet?.has_private_key ? "text-[color:var(--success)]" : "text-[color:var(--danger)]"}>{wallet?.has_private_key ? "已配置" : "未配置"}</strong></span>
                  <span>OKX API: <strong className={wallet?.has_okx_api_key ? "text-[color:var(--success)]" : "text-[color:var(--danger)]"}>{wallet?.has_okx_api_key ? "已配置" : "未配置"}</strong></span>
                  <span>基础代币: <strong>{baseToken}</strong></span>
                </div>
                <div className="flex flex-wrap gap-4 text-xs text-muted-foreground">
                  {baseBalance != null && (
                    <span>{baseToken} 余额: <strong>${baseBalance.toFixed(4)}</strong></span>
                  )}
                  {ethBalance != null && (
                    <span>ETH 余额: <strong>{ethBalance.toFixed(4)}</strong></span>
                  )}
                </div>
              </div>
              {/* 右侧：状态指示 + 编辑按钮 */}
              <div className="flex flex-wrap items-center gap-3">
                <div className="flex items-center gap-2">
                  <span className={cn("size-2 rounded-full", copyEnabled ? "bg-[color:var(--success)]" : "bg-muted-foreground/40")} />
                  <span className="text-xs text-muted-foreground">跟单 {copyEnabled ? "运行中" : "未启用"}</span>
                </div>
                <div className="flex items-center gap-2">
                  <span className={cn("size-2 rounded-full", gridEnabled ? "bg-[color:var(--success)]" : "bg-muted-foreground/40")} />
                  <span className="text-xs text-muted-foreground">网格 {gridEnabled ? "运行中" : "未启用"}</span>
                </div>
                <div className="flex items-center gap-2">
                  <span className={cn("size-2 rounded-full", isWalletReady ? "bg-[color:var(--success)]" : "bg-[color:var(--danger)]")} />
                  <span className="text-xs text-muted-foreground">钱包 {isWalletReady ? "就绪" : "未就绪"}</span>
                </div>
                <Button variant="outline" size="sm" asChild>
                  <Link to="/wallet">
                    <Settings2 className="size-3.5" />
                    配置
                  </Link>
                </Button>
              </div>
            </div>
          </CardContent>
        </Card>
      </section>

      {/* ── 跟单状态 ── */}
      <section>
        <div className="mb-4 flex items-center gap-3">
          <div className="h-px flex-1 bg-border/40" />
          <span className="text-[11px] font-semibold uppercase tracking-[0.22em] text-muted-foreground/60">
            跟单
          </span>
          <div className="h-px flex-1 bg-border/40" />
        </div>
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <MetricCard label="跟单目标" value={String(config?.copy_targets?.length ?? 0)} hint="已配置的跟单钱包数量" />
          <MetricCard label="今日交易" value={String(stats?.today?.total ?? 0)} hint={`成功 ${stats?.today?.success ?? 0} 笔`} />
          <MetricCard label="今日 PnL" value={`$${todayPnl.toFixed(2)}`} hint="今日盈亏" tone={todayPnl >= 0 ? "success" : "danger"} />
          <MetricCard label="持仓数量" value={String(openCount)} hint="当前持有仓位" />
        </div>
        <div className="mt-4 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {baseBalance != null ? (
            <MetricCard label={`${baseToken} 余额`} value={`${baseBalance.toFixed(4)}`} hint="基础交易代币" />
          ) : (
            <MetricCard label={`${baseToken} 余额`} value="-" hint="查询失败" />
          )}
          {ethBalance != null ? (
            <MetricCard label="ETH 余额" value={`${ethBalance.toFixed(4)}`} hint="Gas 代币" />
          ) : (
            <MetricCard label="ETH 余额" value="-" hint="查询失败" />
          )}
          <MetricCard label="累计 PnL" value={`$${(stats?.all?.realized_pnl ?? 0).toFixed(2)}`} hint="已实现盈亏" tone={(stats?.all?.realized_pnl ?? 0) >= 0 ? "success" : "danger"} />
          <MetricCard label="运行模式" value={config?.dry_run ? "Dry Run" : "Live"} hint={`${baseToken} · ${config?.trade_mode ?? "-"}`} tone={config?.dry_run ? "warning" : "success"} />
        </div>
      </section>

      {/* ── 策略状态 ── */}
      <section>
        <div className="mb-4 flex items-center gap-3">
          <div className="h-px flex-1 bg-border/40" />
          <span className="text-[11px] font-semibold uppercase tracking-[0.22em] text-muted-foreground/60">
            策略
          </span>
          <div className="h-px flex-1 bg-border/40" />
        </div>
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <MetricCard
            label="网格策略"
            value={gridEnabled ? "运行中" : "未启用"}
            hint={gridState?.token_symbol ?? ""}
            tone={gridEnabled ? "success" : "default"}
          />
          <MetricCard label="总投入" value={gridState ? `$${gridState.total_investment.toFixed(2)}` : "-"} hint={`${gridState?.total_slots ?? 0} 个网格位`} />
          <MetricCard
            label="活跃位"
            value={gridState ? `${gridActiveSlots} / ${gridState.total_slots}` : "-"}
            hint={gridActiveSlots > 0 ? `${gridActiveSlots} 个已买入` : "等待价格触发"}
            tone={gridActiveSlots > 0 ? "success" : "default"}
          />
          <MetricCard
            label="策略 PnL"
            value={gridState ? `${gridState.total_pnl >= 0 ? "+" : ""}$${gridState.total_pnl.toFixed(2)}` : "-"}
            hint={`已实现 $${gridState?.realized_pnl.toFixed(2) ?? "0"} · 未实现 $${gridState?.unrealized_pnl.toFixed(2) ?? "0"}`}
            tone={(gridState?.total_pnl ?? 0) >= 0 ? "success" : "danger"}
          />
        </div>
      </section>

      {/* ── 跟单钱包列表 ── */}
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
