import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { fetchGridState, fetchGridHistory, toggleExecution } from "@/lib/api";
import type { GridState, GridHistoryResponse } from "@/types/api";
import { PageHeader, MetricCard, SectionCard, LoadingState, EmptyState } from "@/components/app-primitives";
import GridVisualizer from "@/components/GridVisualizer";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { tokenDisplayName, formatTime } from "@/lib/tokens";
import { Power, PowerOff } from "lucide-react";
import { toast } from "sonner";

export default function Grid() {
  const qc = useQueryClient();
  const { data: state, isLoading } = useQuery<GridState>({
    queryKey: ["grid-state"],
    queryFn: fetchGridState,
    refetchInterval: 30000, // 30秒刷新一次
    staleTime: 15000,
  });

  const { data: history } = useQuery<GridHistoryResponse>({
    queryKey: ["grid-history"],
    queryFn: fetchGridHistory,
    refetchInterval: 60000, // 1分钟刷新一次
    staleTime: 30000,
  });

  const toggleVolMut = useMutation({
    mutationFn: toggleExecution,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["grid-state"] });
      toast.success("波动率自适应已切换");
    },
    onError: (e: Error) => toast.error(`切换失败: ${e.message}`),
  });

  if (isLoading) return <LoadingState label="正在加载网格策略..." />;
  if (!state) return <EmptyState title="网格策略未启动" description="后端网格策略尚未初始化，请检查配置。如需帮助，请联系开发人员。" />;

  const trades = history?.trades ?? [];
  const tokenSymbol = state.token_symbol || tokenDisplayName(state.token);
  const activeSlots = state.slots.filter((s) => s.status === "bought").length;
  const volEnabled = state.volatility_adjust;

  return (
    <div className="space-y-6">
      <PageHeader
        eyebrow="strategy"
        title="网格策略"
        description="价格网格自动低买高卖，震荡市持续套利"
      />

      {/* ── 波动率自适应控制 ── */}
      <section>
        <div className="mb-4 flex items-center gap-3">
          <div className="h-px flex-1 bg-border/40" />
          <span className="text-[11px] font-semibold uppercase tracking-[0.22em] text-muted-foreground/60">
            波动率自适应
          </span>
          <div className="h-px flex-1 bg-border/40" />
        </div>
        <Card>
          <CardContent className="p-5">
            <div className="flex items-center justify-between">
              <div className="space-y-1">
                <p className="text-sm font-medium">根据市场波动率自动调整网格间距</p>
                <p className="text-xs text-muted-foreground">
                  {volEnabled
                    ? "高波动时放宽价差（减少无效触发），低波动时收紧价差（增加交易频率）"
                    : "关闭后使用固定价差（config.yaml 中 spread_pct）"}
                </p>
              </div>
              <Button
                variant={volEnabled ? "default" : "outline"}
                size="sm"
                disabled={toggleVolMut.isPending}
                onClick={() => toggleVolMut.mutate({ grid_volatility_adjust: !volEnabled })}
                className="shrink-0"
              >
                {volEnabled ? <Power className="size-4" /> : <PowerOff className="size-4" />}
                {volEnabled ? "已开启" : "已关闭"}
              </Button>
            </div>
          </CardContent>
        </Card>
      </section>

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <MetricCard label="总投入" value={`$${state.total_investment.toFixed(2)}`} hint={`${state.total_slots} 个网格位`} />
        <MetricCard
          label="活跃位"
          value={`${activeSlots} / ${state.total_slots}`}
          hint={activeSlots > 0 ? `${activeSlots} 个已买入待卖出` : "等待价格触发"}
          tone={activeSlots > 0 ? "success" : "default"}
        />
        <MetricCard
          label="总成交"
          value={String(trades.length)}
          hint="网格累计成交次数"
        />
        <MetricCard
          label="总 PnL"
          value={`${state.total_pnl >= 0 ? "+" : ""}$${state.total_pnl.toFixed(2)}`}
          hint={`已实现 $${state.realized_pnl.toFixed(2)} · 未实现 $${state.unrealized_pnl.toFixed(2)}`}
          tone={state.total_pnl >= 0 ? "success" : "danger"}
        />
      </div>

      <SectionCard title="价格网格" description={`${tokenSymbol} 当前价格 $${state.current_price?.toFixed(4) ?? "—"}`}>
        <GridVisualizer slots={state.slots} currentPrice={state.current_price} />
      </SectionCard>

      <SectionCard title="网格明细" description={`共 ${state.slots.length} 个网格位`}>
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Slot</TableHead>
              <TableHead>买入价</TableHead>
              <TableHead>卖出价</TableHead>
              <TableHead>投入</TableHead>
              <TableHead>状态</TableHead>
              <TableHead>当前价值</TableHead>
              <TableHead>ROI</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {state.slots.map((slot) => (
              <TableRow key={slot.slot_id}>
                <TableCell className="font-mono">#{slot.slot_id}</TableCell>
                <TableCell className="font-mono">${slot.buy_price.toFixed(4)}</TableCell>
                <TableCell className="font-mono">${slot.sell_price.toFixed(4)}</TableCell>
                <TableCell>${slot.amount_usdc.toFixed(2)}</TableCell>
                <TableCell>
                  <Badge variant={slot.status === "bought" ? "success" : "default"}>
                    {slot.status === "bought" ? "已买入" : "等待"}
                  </Badge>
                </TableCell>
                <TableCell className="font-mono">
                  {slot.current_value_usd != null ? `$${slot.current_value_usd.toFixed(4)}` : "-"}
                </TableCell>
                <TableCell
                  className="font-mono"
                  style={{ color: slot.roi_pct != null ? (slot.roi_pct >= 0 ? "var(--success)" : "var(--danger)") : undefined }}
                >
                  {slot.roi_pct != null ? `${slot.roi_pct >= 0 ? "+" : ""}${slot.roi_pct}%` : "-"}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </SectionCard>

      {trades.length > 0 && (
        <SectionCard title="成交历史" description={`最近 ${trades.length} 笔网格交易`}>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>时间</TableHead>
                <TableHead>方向</TableHead>
                <TableHead>数量</TableHead>
                <TableHead>金额</TableHead>
                <TableHead>PnL</TableHead>
                <TableHead>ROI</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {trades.map((t, i) => (
                <TableRow key={i}>
                  <TableCell className="text-xs text-muted-foreground">{formatTime(t.created_at)}</TableCell>
                  <TableCell>
                    <Badge variant={t.side === "sell" ? "success" : "default"}>
                      {t.side === "sell" ? "卖出" : "买入"}
                    </Badge>
                  </TableCell>
                  <TableCell className="font-mono text-xs">
                    {t.side === "sell" ? t.amount_in_raw.toFixed(4) : t.amount_out_raw.toFixed(4)} {tokenSymbol}
                  </TableCell>
                  <TableCell className="font-mono text-xs">${t.cost_usd.toFixed(2)}</TableCell>
                  <TableCell
                    className="font-mono text-xs"
                    style={{ color: t.pnl_usd >= 0 ? "var(--success)" : "var(--danger)" }}
                  >
                    {t.pnl_usd >= 0 ? "+" : ""}${t.pnl_usd.toFixed(2)}
                  </TableCell>
                  <TableCell
                    className="font-mono text-xs"
                    style={{ color: t.roi_pct >= 0 ? "var(--success)" : "var(--danger)" }}
                  >
                    {t.roi_pct >= 0 ? "+" : ""}{t.roi_pct}%
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
