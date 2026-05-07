import { useQuery } from "@tanstack/react-query";
import { fetchMeanReversionState } from "@/lib/api";
import type { MeanReversionState, MrSymbolState } from "@/types/api";
import { PageHeader, MetricCard, SectionCard, StatusBadge, LoadingState, EmptyState } from "@/components/app-primitives";
import { Card, CardContent } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Check, X, TrendingUp, TrendingDown, Minus, AlertTriangle } from "lucide-react";
import { cn } from "@/lib/utils";

function formatPrice(v: number): string {
  if (v >= 1) return v.toFixed(2);
  if (v >= 0.001) return v.toFixed(6);
  return v.toFixed(8);
}

function formatPct(v: number): string {
  return `${v >= 0 ? "+" : ""}${v.toFixed(2)}%`;
}

function EntryConditions({ symbol }: { symbol: MrSymbolState }) {
  const okCount = symbol.entry_conditions.filter((c) => c.ok).length;
  const total = symbol.entry_conditions.length;
  return (
    <SectionCard title={`${symbol.symbol} 入场信号`} description={`${okCount}/${total} 条件满足`}>
      {symbol.has_position ? (
        <div className="rounded-xl bg-[var(--success-soft)] px-4 py-3 text-center text-sm font-medium text-[var(--success-foreground)]">
          已持仓（{symbol.signal_strength === "strong" ? "强信号" : "中信号"}）
        </div>
      ) : (
        <div className="space-y-3">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="w-8" />
                <TableHead>条件</TableHead>
                <TableHead>当前值</TableHead>
                <TableHead>阈值</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {symbol.entry_conditions.map((c, i) => (
                <TableRow key={i}>
                  <TableCell>
                    {c.ok
                      ? <Check className="size-4 text-[var(--success)]" />
                      : <X className="size-4 text-[var(--danger)]" />
                    }
                  </TableCell>
                  <TableCell className="text-xs">{c.label}</TableCell>
                  <TableCell className="font-mono text-xs">
                    {typeof c.current === "boolean" ? (c.current ? "是" : "否") : c.current.toFixed(1)}
                  </TableCell>
                  <TableCell className="font-mono text-xs text-muted-foreground">{c.threshold}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
          {okCount === total && (
            <div className="rounded-xl bg-[var(--success-soft)] px-4 py-2 text-center text-sm font-medium text-[var(--success-foreground)]">
              ★ 强信号：3/3 全部满足，可入场 3%
            </div>
          )}
          {okCount >= 2 && okCount < total && (
            <div className="rounded-xl bg-[var(--warning-soft)] px-4 py-2 text-center text-sm font-medium text-[var(--warning-foreground)]">
              △ 中信号：2/3 满足（缺 MACD），可入场 1.5%
            </div>
          )}
        </div>
      )}
    </SectionCard>
  );
}

function PositionCard({ symbol }: { symbol: MrSymbolState }) {
  const p = symbol.position;
  if (!p) return null;

  const hasStop = p.pnl_pct <= -((symbol.indicators.atr || 1) / (p.entry_price || 1) * 100);
  const hasTP1 = p.pnl_pct >= (symbol.indicators.atr || 0) / (p.entry_price || 1) * 1.5 * 100;

  return (
    <Card>
      <CardContent className="p-5">
        <div className="mb-3 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <span className="text-lg font-semibold">{p.symbol}</span>
            <span className={cn(
              "rounded-full px-2 py-0.5 text-[11px] font-medium",
              p.entry_signal === "strong" ? "bg-[var(--success-soft)] text-[var(--success-foreground)]" : "bg-[var(--warning-soft)] text-[var(--warning-foreground)]",
            )}>
              {p.entry_signal === "strong" ? "强信号" : "中信号"}
            </span>
          </div>
          <span className={cn("text-lg font-bold tabular-nums", p.pnl_pct >= 0 ? "text-[var(--success)]" : "text-[var(--danger)]")}>
            {formatPct(p.pnl_pct)}
          </span>
        </div>

        <div className="grid grid-cols-2 gap-3 text-sm">
          <div>
            <span className="text-muted-foreground">入场价 </span>
            <span className="font-mono">${formatPrice(p.entry_price)}</span>
          </div>
          <div>
            <span className="text-muted-foreground">当前价 </span>
            <span className="font-mono">${formatPrice(p.current_price)}</span>
          </div>
          <div>
            <span className="text-muted-foreground">数量 </span>
            <span className="font-mono">{p.amount.toFixed(4)}</span>
          </div>
          <div>
            <span className="text-muted-foreground">价值 </span>
            <span className="font-mono">${p.position_value_usdc.toFixed(2)}</span>
          </div>
          <div>
            <span className="text-muted-foreground">持仓时长 </span>
            <span className="font-mono">{p.holding_hours.toFixed(1)}h</span>
          </div>
          <div>
            <span className="text-muted-foreground">成本 </span>
            <span className="font-mono">${p.cost_basis_usdc.toFixed(2)}</span>
          </div>
        </div>

        {/* 止盈进度 */}
        <div className="mt-4 flex gap-3">
          <div className={cn("flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-medium", p.tp1_done ? "bg-[var(--success-soft)] text-[var(--success-foreground)]" : "bg-muted text-muted-foreground")}>
            {p.tp1_done ? <Check className="size-3" /> : <Minus className="size-3" />}
            TP1 {p.tp1_done ? "已完成" : "待触发"}
          </div>
          <div className={cn("flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-medium", p.tp2_done ? "bg-[var(--success-soft)] text-[var(--success-foreground)]" : "bg-muted text-muted-foreground")}>
            {p.tp2_done ? <Check className="size-3" /> : <Minus className="size-3" />}
            TP2 {p.tp2_done ? "已完成" : "待触发"}
          </div>
          <div className={cn("flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-medium", p.trailing_stop_active ? "bg-[var(--warning-soft)] text-[var(--warning-foreground)]" : "bg-muted text-muted-foreground")}>
            {p.trailing_stop_active ? <TrendingUp className="size-3" /> : <Minus className="size-3" />}
            {p.trailing_stop_active ? "移动止损已激活" : "移动止损待激活"}
          </div>
          {hasStop && (
            <div className="flex items-center gap-1.5 rounded-lg bg-[var(--danger-soft)] px-3 py-1.5 text-xs font-medium text-[var(--danger-foreground)]">
              <AlertTriangle className="size-3" />
              硬止损触发中
            </div>
          )}
        </div>
      </CardContent>
    </Card>
  );
}

function IndicatorGrid({ symbol }: { symbol: MrSymbolState }) {
  const ind = symbol.indicators;
  if (!ind || !Object.keys(ind).length) return null;

  // 趋势方向指示器
  const TrendIcon = ind.sma_200_direction === "up" ? TrendingUp : TrendingDown;
  const trendColor = ind.sma_200_direction === "up" ? "text-[var(--success)]" : "text-[var(--danger)]";

  return (
    <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
      <MetricCard label="RSI(14)" value={ind.rsi.toFixed(1)} hint={ind.rsi < 40 ? "超卖区 ✓" : "正常区间"} tone={ind.rsi < 40 ? "success" : "default"} />
      <MetricCard label="ATR(14)" value={`$${ind.atr.toFixed(2)}`} hint={`${(ind.atr / (ind.current_price || 1) * 100).toFixed(2)}%`} />
      <MetricCard label="90天百分位" value={`${ind.price_percentile_90d.toFixed(1)}%`} hint={ind.price_percentile_90d <= 30 ? "低位 ✓" : "中高位"} tone={ind.price_percentile_90d <= 30 ? "success" : "default"} />
      <MetricCard
        label="200MA"
        value={`$${formatPrice(ind.sma_200)}`}
        hint={
          <span className={cn("inline-flex items-center gap-1", trendColor)}>
            <TrendIcon className="size-3" />
            {ind.sma_200_direction === "up" ? "向上" : "向下"}
          </span>
        }
        tone={ind.sma_200_direction === "up" ? "success" : "danger"}
      />
      <MetricCard label="MACD 线" value={ind.macd_line.toFixed(4)} hint={`信号 ${ind.macd_signal.toFixed(4)}`} />
      <MetricCard
        label="MACD 金叉"
        value={ind.macd_golden_cross ? "是" : "否"}
        hint={ind.macd_golden_cross ? "已金叉 ✓" : "等待中"}
        tone={ind.macd_golden_cross ? "success" : "default"}
      />
    </div>
  );
}

export default function MeanReversion() {
  const { data: state, isLoading } = useQuery<MeanReversionState>({
    queryKey: ["mean-reversion-state"],
    queryFn: fetchMeanReversionState,
    refetchInterval: 30000,
    staleTime: 15000,
  });

  if (isLoading) return <LoadingState label="正在加载均值回归策略..." />;
  if (!state) return <EmptyState title="策略未启动" description="均值回归策略尚未初始化，请检查 config.yaml 中 mean_reversion.enabled 配置。" />;

  const hasPaused = state.paused_until && new Date(state.paused_until) > new Date();
  const totalPositions = state.symbols.filter((s) => s.has_position).length;

  return (
    <div className="space-y-6">
      <PageHeader
        eyebrow="strategy"
        title="双锚稳定套利"
        description="均值回归策略：90 天价格百分位 + RSI + MACD 入场，ATR 自适应出场"
      />

      {/* ── 状态栏 ── */}
      <section className="flex flex-wrap gap-4">
        <StatusBadge ok={!hasPaused} label="策略状态" hint={hasPaused ? "暂停中" : "运行中"} />
        <StatusBadge ok={state.consecutive_losses < state.config?.consecutive_loss_pause} label="连续亏损" hint={`${state.consecutive_losses}/${state.config?.consecutive_loss_pause || 3}`} />
        <StatusBadge ok={state.daily_open_count < (state.config?.max_daily_open as number || 2)} label="今日开仓" hint={`${state.daily_open_count}/${state.config?.max_daily_open || 2}`} />
        <StatusBadge ok={totalPositions < (state.config?.max_positions as number || 6)} label="当前持仓" hint={`${totalPositions}/${state.config?.max_positions || 6}`} />
      </section>

      {/* ── 持仓面板 ── */}
      {totalPositions > 0 && (
        <SectionCard title="当前持仓" description={`${totalPositions} 个持仓`}>
          <div className="grid gap-4">
            {state.symbols.filter((s) => s.has_position).map((s) => (
              <PositionCard key={s.symbol} symbol={s} />
            ))}
          </div>
        </SectionCard>
      )}

      {/* ── 各标的信号与指标 ── */}
      {state.symbols.map((s) => (
        <div key={s.symbol} className="space-y-4">
          <IndicatorGrid symbol={s} />
          <EntryConditions symbol={s} />
        </div>
      ))}
    </div>
  );
}
