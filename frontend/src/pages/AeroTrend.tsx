import { useQuery } from "@tanstack/react-query";
import { fetchAeroState } from "@/lib/api";
import type { AeroState } from "@/types/api";
import { PageHeader, MetricCard, SectionCard, StatusBadge, LoadingState, EmptyState } from "@/components/app-primitives";
import { Card, CardContent } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { TrendingUp, Layers, AlertTriangle } from "lucide-react";
import { cn } from "@/lib/utils";

function formatPct(v: number): string {
  return `${v >= 0 ? "+" : ""}${(v * 100).toFixed(2)}%`;
}

export default function AeroTrend() {
  const { data: state, isLoading } = useQuery<AeroState>({
    queryKey: ["aero-state"],
    queryFn: fetchAeroState,
    refetchInterval: 30000,
    staleTime: 15000,
  });

  if (isLoading) return <LoadingState label="正在加载 AERO 趋势策略..." />;
  if (!state) return <EmptyState title="AERO 策略未启动" description="后端 AERO 趋势策略尚未初始化，请检查 config.yaml 中 aero_trend.enabled 配置。" />;

  const pos = state.has_position;

  return (
    <div className="space-y-6">
      <PageHeader
        eyebrow="strategy"
        title="AERO 趋势策略"
        description="Base 链 AERO 代币趋势交易：价格动量 + 成交量放大 + 流动性质量"
      />

      {/* ── 状态栏 ── */}
      <section className="flex flex-wrap gap-4">
        <StatusBadge
          ok={pos}
          label="持仓状态"
          hint={pos ? `已持仓 ${state.holding_time_minutes} 分钟` : "当前无持仓"}
        />
        <StatusBadge
          ok={state.consecutive_losses < 3}
          label="风控状态"
          hint={
            state.consecutive_losses >= 5
              ? `连续亏损 ${state.consecutive_losses} 次，已停止交易`
              : state.consecutive_losses >= 3
                ? `连续亏损 ${state.consecutive_losses} 次，仓位已减半`
                : `连续亏损 ${state.consecutive_losses} 次`
          }
        />
      </section>

      {/* ── 核心指标 ── */}
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <MetricCard
          label="当前价格"
          value={`$${state.current_price.toFixed(6)}`}
          hint={`入场价 $${state.entry_price.toFixed(6)}`}
          tone={pos ? (state.pnl_pct >= 0 ? "success" : "danger") : "default"}
        />
        <MetricCard
          label="持仓浮盈"
          value={pos ? formatPct(state.pnl_pct) : "—"}
          hint={pos ? `$${((state.position_amount * state.current_price) - state.cost_basis_usdc).toFixed(2)}` : "无持仓"}
          tone={pos ? (state.pnl_pct >= 0 ? "success" : "danger") : "default"}
        />
        <MetricCard
          label="持仓数量"
          value={pos ? state.position_amount.toFixed(2) : "—"}
          hint={`价值 $${(state.position_amount * state.current_price).toFixed(2)}`}
        />
        <MetricCard
          label="持仓时间"
          value={pos ? `${state.holding_time_minutes}min` : "—"}
          hint={pos ? `最高价 $${state.highest_price.toFixed(6)}` : "等待买入信号"}
        />
      </div>

      {/* ── 止盈状态 ── */}
      {pos && (
        <SectionCard title="止盈进度" description="分批止盈 + 移动止盈追踪">
          <div className="grid gap-4 sm:grid-cols-3">
            <Card className={cn(state.take_profit_1_done ? "border-success/40" : "opacity-40")}>
              <CardContent className="p-5">
                <div className="flex items-center gap-3">
                  <Layers className={cn("size-5", state.take_profit_1_done ? "text-success" : "text-muted-foreground")} />
                  <div>
                    <div className="text-sm font-medium">TP1 (10%)</div>
                    <div className="text-xs text-muted-foreground">
                      {state.take_profit_1_done ? "已完成" : "等待触发"}
                    </div>
                  </div>
                </div>
              </CardContent>
            </Card>
            <Card className={cn(state.take_profit_2_done ? "border-success/40" : state.take_profit_1_done ? "" : "opacity-40")}>
              <CardContent className="p-5">
                <div className="flex items-center gap-3">
                  <Layers className={cn("size-5", state.take_profit_2_done ? "text-success" : "text-muted-foreground")} />
                  <div>
                    <div className="text-sm font-medium">TP2 (20%)</div>
                    <div className="text-xs text-muted-foreground">
                      {state.take_profit_2_done ? "已完成" : state.take_profit_1_done ? "等待触发" : "等待 TP1"}
                    </div>
                  </div>
                </div>
              </CardContent>
            </Card>
            <Card className={cn(state.trailing_stop_active ? "border-warning/40" : "opacity-40")}>
              <CardContent className="p-5">
                <div className="flex items-center gap-3">
                  <AlertTriangle className={cn("size-5", state.trailing_stop_active ? "text-warning" : "text-muted-foreground")} />
                  <div>
                    <div className="text-sm font-medium">移动止盈</div>
                    <div className="text-xs text-muted-foreground">
                      {state.trailing_stop_active ? "已启用" : "等待 TP2"}
                    </div>
                  </div>
                </div>
              </CardContent>
            </Card>
          </div>
        </SectionCard>
      )}

      {/* ── 持仓明细 ── */}
      {pos && (
        <SectionCard title="持仓明细" description={`买入价格 $${state.entry_price.toFixed(6)}`}>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>指标</TableHead>
                <TableHead>数值</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              <TableRow>
                <TableCell>持仓数量</TableCell>
                <TableCell className="font-mono">{state.position_amount.toFixed(4)} AERO</TableCell>
              </TableRow>
              <TableRow>
                <TableCell>成本价</TableCell>
                <TableCell className="font-mono">${state.entry_price.toFixed(6)}</TableCell>
              </TableRow>
              <TableRow>
                <TableCell>当前价</TableCell>
                <TableCell className="font-mono">${state.current_price.toFixed(6)}</TableCell>
              </TableRow>
              <TableRow>
                <TableCell>持仓价值</TableCell>
                <TableCell className="font-mono">${(state.position_amount * state.current_price).toFixed(2)}</TableCell>
              </TableRow>
              <TableRow>
                <TableCell>投入成本</TableCell>
                <TableCell className="font-mono">${state.cost_basis_usdc.toFixed(2)}</TableCell>
              </TableRow>
              <TableRow>
                <TableCell>浮盈</TableCell>
                <TableCell className="font-mono" style={{ color: state.pnl_pct >= 0 ? "var(--success)" : "var(--danger)" }}>
                  {formatPct(state.pnl_pct)}
                </TableCell>
              </TableRow>
              <TableRow>
                <TableCell>最高价</TableCell>
                <TableCell className="font-mono">${state.highest_price.toFixed(6)}</TableCell>
              </TableRow>
              <TableRow>
                <TableCell>持仓时间</TableCell>
                <TableCell className="font-mono">{state.holding_time_minutes} 分钟</TableCell>
              </TableRow>
              <TableRow>
                <TableCell>入场交易</TableCell>
                <TableCell className="font-mono text-xs">{state.buy_tx_hash ? state.buy_tx_hash.slice(0, 20) + "..." : "—"}</TableCell>
              </TableRow>
            </TableBody>
          </Table>
        </SectionCard>
      )}

      {/* ── 无持仓提示 ── */}
      {!pos && (
        <Card>
          <CardContent className="p-8 text-center text-muted-foreground">
            <TrendingUp className="mx-auto mb-3 size-8 opacity-40" />
            <p className="text-sm">当前无 AERO 持仓，系统正在扫描趋势信号...</p>
            <p className="mt-1 text-xs opacity-60">满足 11 个条件后自动买入，按 P0-P4 优先级执行卖出</p>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
