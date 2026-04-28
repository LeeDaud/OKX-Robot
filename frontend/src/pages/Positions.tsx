import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Copy, Check } from "lucide-react";
import { toast } from "sonner";
import { fetchPositionsAll } from "@/lib/api";
import type { PositionAllResponse } from "@/types/api";
import { PageHeader, SectionCard, MetricCard, LoadingState, EmptyState } from "@/components/app-primitives";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { tokenDisplayName, formatTokenAmount } from "@/lib/tokens";

function CopyAddress({ address }: { address: string }) {
  const [copied, setCopied] = useState(false);
  const display = tokenDisplayName(address);
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
      <span className="font-mono text-xs font-semibold">{display}</span>
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

export default function Positions() {
  const { data, isLoading } = useQuery<PositionAllResponse>({
    queryKey: ["positions-all"],
    queryFn: fetchPositionsAll,
    refetchInterval: 15000,
  });

  if (isLoading) return <LoadingState label="正在加载持仓数据..." />;

  const open = data?.open ?? [];
  const closed = data?.closed ?? [];
  const summary = data?.summary;

  return (
    <div className="space-y-6">
      <PageHeader title="持仓管理" description="当前持仓与历史持仓概览" />

      {summary && (
        <div className="grid gap-4 sm:grid-cols-4">
          <MetricCard label="当前持仓" value={String(summary.open_count)} hint="未平仓数量" />
          <MetricCard label="投入本金" value={`$${summary.total_invested_open.toFixed(2)}`} hint="当前持仓总投入" />
          <MetricCard label="已平仓" value={String(summary.closed_count)} hint="历史平仓笔数" />
          <MetricCard label="已实现 PnL" value={`$${summary.realized_pnl.toFixed(2)}`} hint="全部已实现盈亏" tone={summary.realized_pnl >= 0 ? "success" : "danger"} />
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
                <TableHead>数量</TableHead>
                <TableHead>入场价</TableHead>
                <TableHead>投入成本</TableHead>
                <TableHead>未实现 PnL</TableHead>
                <TableHead>开仓时间</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {open.map((p) => (
                <TableRow key={p.id}>
                  <TableCell>
                    <CopyAddress address={p.token_out || ""} />
                  </TableCell>
                  <TableCell className="font-mono text-xs">
                    {p.amount_out ? formatTokenAmount(p.amount_out) : "-"}
                  </TableCell>
                  <TableCell>${p.entry_price?.toFixed(6) ?? "-"}</TableCell>
                  <TableCell>${(p.filled_cost_usd ?? 0).toFixed(2)}</TableCell>
                  <TableCell className="text-xs text-muted-foreground">
                    —<span className="ml-1 text-[10px]">需链上报价</span>
                  </TableCell>
                  <TableCell className="text-xs text-muted-foreground whitespace-nowrap">
                    {p.created_at?.slice(11, 19) || "-"}
                  </TableCell>
                </TableRow>
              ))}
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
                  <TableCell>
                    <CopyAddress address={p.token_out || ""} />
                  </TableCell>
                  <TableCell className="font-mono text-xs">
                    {p.amount_out ? formatTokenAmount(p.amount_out) : "-"}
                  </TableCell>
                  <TableCell>${p.entry_price?.toFixed(6) ?? "-"}</TableCell>
                  <TableCell>${p.exit_price?.toFixed(6) ?? "-"}</TableCell>
                  <TableCell>
                    <span style={{ color: (p.roi_pct || 0) >= 0 ? "var(--success)" : "var(--danger)" }}>
                      {p.roi_pct != null ? `${(p.roi_pct * 100).toFixed(1)}%` : "-"}
                    </span>
                  </TableCell>
                  <TableCell className="font-semibold" style={{ color: (p.pnl || 0) >= 0 ? "var(--success)" : "var(--danger)" }}>
                    {p.pnl != null ? `$${p.pnl.toFixed(2)}` : "-"}
                  </TableCell>
                  <TableCell className="text-xs text-muted-foreground whitespace-nowrap">
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
