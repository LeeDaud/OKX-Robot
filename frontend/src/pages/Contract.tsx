import { useQuery } from "@tanstack/react-query";
import { fetchContractState } from "@/lib/api";
import type { ContractState, ContractPosition } from "@/types/api";
import { PageHeader, MetricCard, SectionCard, StatusBadge, LoadingState, EmptyState } from "@/components/app-primitives";
import { Card, CardContent } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Wallet, Banknote, ArrowRightLeft } from "lucide-react";
import { cn } from "@/lib/utils";

function formatUsd(v: number): string {
  return `$${v.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

export default function Contract() {
  const { data: state, isLoading } = useQuery<ContractState>({
    queryKey: ["contract-state"],
    queryFn: fetchContractState,
    refetchInterval: 30000,
    staleTime: 15000,
  });

  if (isLoading) return <LoadingState label="正在加载合约交易面板..." />;
  if (!state) return <EmptyState title="合约交易未启动" description="请检查 config.yaml 中 contract.enabled 配置，并确保 RPC 连接正常。" />;

  const { balances, positions } = state;
  const hasPos = positions.length > 0;

  return (
    <div className="space-y-6">
      <PageHeader
        eyebrow="strategy"
        title="合约交易"
        description={`Base 链 SynFutures V3 永续合约 ${state.dry_run ? "· 模拟模式" : "· 实盘模式"}`}
      />

      {/* ── 状态栏 ── */}
      <section className="flex flex-wrap gap-4">
        <StatusBadge
          ok={state.enabled}
          label="策略状态"
          hint={state.enabled ? "已启用" : "已禁用"}
        />
        <StatusBadge
          ok={!state.dry_run}
          label="执行模式"
          hint={state.dry_run ? "模拟交易（不发送链上交易）" : "实盘交易"}
        />
        <StatusBadge
          ok={hasPos}
          label="持仓状态"
          hint={hasPos ? `${positions.length} 个持仓` : "当前无持仓"}
        />
      </section>

      {/* ── 余额卡片 ── */}
      <div className="grid gap-4 sm:grid-cols-3">
        <MetricCard
          label="Vault USDC"
          value={formatUsd(balances.vault_usdc)}
          hint="合约保证金账户"
          tone="default"
          icon={Banknote}
        />
        <MetricCard
          label="钱包 USDC"
          value={formatUsd(balances.wallet_usdc)}
          hint="链上钱包余额"
          tone="default"
          icon={Wallet}
        />
        <MetricCard
          label="总 USDC"
          value={formatUsd(balances.total_usdc)}
          hint="Vault + 钱包"
          tone={balances.total_usdc > 0 ? "success" : "default"}
        />
      </div>

      {/* ── 持仓列表 ── */}
      <SectionCard title="持仓列表" description={hasPos ? `${positions.length} 个活跃仓位` : "当前无持仓"}>
        {hasPos ? (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>交易对</TableHead>
                <TableHead>方向</TableHead>
                <TableHead>名义价值</TableHead>
                <TableHead>保证金</TableHead>
                <TableHead>标记价格</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {positions.map((pos: ContractPosition) => (
                <TableRow key={pos.pair}>
                  <TableCell className="font-medium">{pos.pair}</TableCell>
                  <TableCell>
                    <span className={cn(
                      "inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-xs font-medium",
                      pos.side === "long"
                        ? "bg-[var(--success-soft)] text-[var(--success-foreground)]"
                        : "bg-[var(--danger-soft)] text-[var(--danger-foreground)]"
                    )}>
                      {pos.side === "long" ? "做多" : "做空"}
                    </span>
                  </TableCell>
                  <TableCell className="font-mono text-xs">{formatUsd(pos.size_usd)}</TableCell>
                  <TableCell className="font-mono text-xs">{formatUsd(pos.margin_usd)}</TableCell>
                  <TableCell className="font-mono text-xs">
                    {pos.mark_price ? `$${pos.mark_price.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}` : "—"}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        ) : (
          <Card>
            <CardContent className="p-8 text-center text-muted-foreground">
              <ArrowRightLeft className="mx-auto mb-3 size-8 opacity-40" />
              <p className="text-sm">当前无合约持仓</p>
              <p className="mt-1 text-xs opacity-60">系统将根据策略信号自动开仓平仓</p>
            </CardContent>
          </Card>
        )}
      </SectionCard>
    </div>
  );
}
