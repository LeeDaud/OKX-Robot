import { useState, useCallback } from "react";
import { useQuery } from "@tanstack/react-query";
import { Copy, Check, RefreshCw } from "lucide-react";
import { toast } from "sonner";
import { fetchPositionsAll, refreshPositionsPrices } from "@/lib/api";
import type { PositionAllResponse } from "@/types/api";
import { PageHeader, SectionCard, MetricCard, LoadingState, EmptyState } from "@/components/app-primitives";
import { Button } from "@/components/ui/button";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { tokenDisplayName, shortenAddress } from "@/lib/tokens";

function CopyAddress({ address }: { address: string }) {
  const [copied, setCopied] = useState(false);
  const display = shortenAddress(address, 6, 4);
  const full = address.toLowerCase();

  const handleCopy = () => {
    navigator.clipboard.writeText(full).then(() => {
      setCopied(true);
      toast.success("地址已复制");
      setTimeout(() => setCopied(false), 2000);
    });
  };

  return (
    <span className="inline-flex items-center gap-1.5">
      <span className="font-mono text-xs text-muted-foreground">{display}</span>
      <button
        onClick={handleCopy}
        className="text-muted-foreground hover:text-foreground transition shrink-0"
        title={full}
      >
        {copied ? <Check className="size-3 text-[var(--success)]" /> : <Copy className="size-3" />}
      </button>
    </span>
  );
}

function AmountCell({ value }: { value: number | string | null | undefined }) {
  if (value == null || value === "-") return <span className="text-xs text-muted-foreground">-</span>;
  const n = typeof value === "string" ? parseFloat(value) : value;
  if (isNaN(n)) return <span className="text-xs text-muted-foreground">-</span>;
  const formatted = n >= 1000 ? n.toFixed(2) : n >= 1 ? n.toFixed(4) : n >= 0.001 ? n.toFixed(6) : n.toFixed(8);
  return <span className="font-mono text-xs font-semibold">{formatted}</span>;
}

function DollarCell({ value, tone }: { value: number | null | undefined; tone?: boolean }) {
  if (value == null) return <span className="text-xs text-muted-foreground">-</span>;
  const style = tone ? { color: value >= 0 ? "var(--success)" : "var(--danger)" } : undefined;
  return (
    <span className="font-mono text-xs font-semibold" style={style}>
      {value >= 0 ? "+$" : "-$"}{Math.abs(value).toFixed(2)}
    </span>
  );
}

function PctCell({ value }: { value: number | null | undefined }) {
  if (value == null) return <span className="text-xs text-muted-foreground">-</span>;
  return (
    <span className="font-mono text-xs font-semibold" style={{ color: value >= 0 ? "var(--success)" : "var(--danger)" }}>
      {value >= 0 ? "+" : ""}{value.toFixed(1)}%
    </span>
  );
}

type PriceData = {
  current_price: number | null
  current_value_usd: number | null
  unrealized_pnl: number | null
  roi_pct: number | null
  amount: number
  cost_basis_usd: number
}

type TokenInfo = {
  symbol: string | null
  decimals: number
  current_price: number | null
}

export default function Positions() {
  const [priceData, setPriceData] = useState<Record<string, PriceData> | null>(null);
  const [tokenInfo, setTokenInfo] = useState<Record<string, TokenInfo> | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  const { data, isLoading } = useQuery<PositionAllResponse>({
    queryKey: ["positions-all"],
    queryFn: fetchPositionsAll,
    refetchInterval: 15000,
  });

  const handleRefresh = useCallback(async () => {
    setRefreshing(true);
    try {
      const result = await refreshPositionsPrices();
      if (result.positions && Object.keys(result.positions).length > 0) {
        setPriceData(result.positions);
        setTokenInfo(result.tokens);
        toast.success("价格已刷新");
      } else {
        toast.error(result.error || "无持仓数据");
      }
    } catch (e: any) {
      toast.error(`价格刷新失败: ${e.message}`);
    } finally {
      setRefreshing(false);
    }
  }, []);

  if (isLoading) return <LoadingState label="正在加载持仓数据..." />;

  const open = data?.open ?? [];
  const closed = data?.closed ?? [];
  const summary = data?.summary;
  const hasPriceData = priceData && Object.keys(priceData).length > 0;

  let totalUnrealizedPnl: number | null = null;
  if (hasPriceData) {
    totalUnrealizedPnl = 0;
    for (const pid in priceData!) {
      const pnl = priceData[pid].unrealized_pnl;
      if (pnl !== null) totalUnrealizedPnl += pnl;
    }
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title="持仓管理"
        description="当前持仓与历史持仓概览"
        actions={
          <Button onClick={handleRefresh} disabled={refreshing}>
            <RefreshCw className={`size-4 ${refreshing ? "animate-spin" : ""}`} />
            {refreshing ? "刷新中..." : "刷新报价"}
          </Button>
        }
      />

      {summary && (
        <div className="grid gap-4 sm:grid-cols-5">
          <MetricCard label="当前持仓" value={String(summary.open_count)} hint="未平仓数量" />
          <MetricCard label="投入本金" value={`$${summary.total_invested_open.toFixed(2)}`} hint="当前持仓总投入" />
          <MetricCard label="已平仓" value={String(summary.closed_count)} hint="历史平仓笔数" />
          <MetricCard label="已实现 PnL" value={`${summary.realized_pnl >= 0 ? "+$" : "-$"}${Math.abs(summary.realized_pnl).toFixed(2)}`} hint="全部已实现盈亏" tone={summary.realized_pnl >= 0 ? "success" : "danger"} />
          {totalUnrealizedPnl !== null && (
            <MetricCard
              label="未实现 PnL"
              value={`${totalUnrealizedPnl >= 0 ? "+$" : "-$"}${Math.abs(totalUnrealizedPnl).toFixed(2)}`}
              hint="当前持仓浮动盈亏"
              tone={totalUnrealizedPnl >= 0 ? "success" : "danger"}
            />
          )}
        </div>
      )}

      <SectionCard title="当前持仓" description={open.length > 0 ? `${open.length} 个未平仓` : undefined}>
        {open.length === 0 ? (
          <EmptyState title="暂无持仓" description="当前没有未平仓的持仓记录" compact />
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>代币</TableHead>
                <TableHead>名称</TableHead>
                <TableHead>来源钱包</TableHead>
                <TableHead>数量</TableHead>
                <TableHead>入场价</TableHead>
                <TableHead>投入成本</TableHead>
                <TableHead>当前价</TableHead>
                <TableHead>当前价值</TableHead>
                <TableHead>未实现 PnL</TableHead>
                <TableHead>ROI</TableHead>
                <TableHead>开仓时间</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {open.map((p) => {
                const pd = priceData?.[String(p.id)];
                const ti = p.token_out ? tokenInfo?.[p.token_out.toLowerCase()] : undefined;
                return (
                  <TableRow key={p.id}>
                    <TableCell><CopyAddress address={p.token_out || ""} /></TableCell>
                    <TableCell className="font-mono text-xs font-semibold">
                      {ti?.symbol || tokenDisplayName(p.token_out || "")}
                    </TableCell>
                    <TableCell><CopyAddress address={p.source_addr || ""} /></TableCell>
                    <TableCell><AmountCell value={pd?.amount ?? p.amount_out} /></TableCell>
                    <TableCell className="font-mono text-xs">${p.entry_price?.toFixed(6) ?? "-"}</TableCell>
                    <TableCell><DollarCell value={p.filled_cost_usd} /></TableCell>
                    <TableCell className="font-mono text-xs">
                      {pd?.current_price != null ? `$${pd.current_price.toFixed(8)}` : "-"}
                    </TableCell>
                    <TableCell><DollarCell value={pd?.current_value_usd} /></TableCell>
                    <TableCell><DollarCell value={pd?.unrealized_pnl} tone /></TableCell>
                    <TableCell><PctCell value={pd?.roi_pct} /></TableCell>
                    <TableCell className="font-mono text-xs text-muted-foreground whitespace-nowrap">
                      {p.created_at?.slice(11, 19) || "-"}
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        )}
      </SectionCard>

      <SectionCard title="历史持仓" description={closed.length > 0 ? `${closed.length} 笔已平仓` : undefined}>
        {closed.length === 0 ? (
          <EmptyState title="暂无历史持仓" description="平仓记录将显示在这里" compact />
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>代币</TableHead>
                <TableHead>名称</TableHead>
                <TableHead>来源钱包</TableHead>
                <TableHead>数量</TableHead>
                <TableHead>入场价</TableHead>
                <TableHead>出场价</TableHead>
                <TableHead>ROI</TableHead>
                <TableHead>PnL</TableHead>
                <TableHead>时间</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {closed.map((p) => (
                <TableRow key={p.id}>
                  <TableCell><CopyAddress address={p.token_out || ""} /></TableCell>
                  <TableCell className="font-mono text-xs font-semibold">
                    {tokenDisplayName(p.token_out || "")}
                  </TableCell>
                  <TableCell><CopyAddress address={p.source_addr || ""} /></TableCell>
                  <TableCell><AmountCell value={p.amount_out} /></TableCell>
                  <TableCell className="font-mono text-xs">${p.entry_price?.toFixed(6) ?? "-"}</TableCell>
                  <TableCell className="font-mono text-xs">${p.exit_price?.toFixed(6) ?? "-"}</TableCell>
                  <TableCell><PctCell value={p.roi_pct != null ? p.roi_pct * 100 : null} /></TableCell>
                  <TableCell><DollarCell value={p.pnl} tone /></TableCell>
                  <TableCell className="font-mono text-xs text-muted-foreground whitespace-nowrap">
                    {p.created_at?.slice(11, 19) || "-"}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </SectionCard>
    </div>
  );
}
