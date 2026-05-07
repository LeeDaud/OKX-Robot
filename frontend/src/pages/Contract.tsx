import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { fetchContractState, openContractPosition, closeContractPosition } from "@/lib/api";
import type { ContractState, ContractPosition } from "@/types/api";
import { PageHeader, MetricCard, SectionCard, StatusBadge, LoadingState, EmptyState } from "@/components/app-primitives";
import { Card, CardContent } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { ArrowLeftRight, TrendingUp, X, Check, Loader2, Wallet, Banknote } from "lucide-react";
import { toast } from "sonner";
import { cn } from "@/lib/utils";

function formatUsd(v: number): string {
  return `$${v.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

export default function Contract() {
  const queryClient = useQueryClient();
  const [pair, setPair] = useState("BTC/USDC");
  const [side, setSide] = useState<"long" | "short">("long");
  const [marginUsd, setMarginUsd] = useState("100");
  const [leverage, setLeverage] = useState("3");

  const { data: state, isLoading } = useQuery<ContractState>({
    queryKey: ["contract-state"],
    queryFn: fetchContractState,
    refetchInterval: 30000,
    staleTime: 15000,
  });

  const openMut = useMutation({
    mutationFn: () =>
      openContractPosition({
        pair,
        side,
        margin_usd: parseFloat(marginUsd) || 0,
        leverage: parseInt(leverage) || 0,
      }),
    onSuccess: (res) => {
      if (res.dry_run || res.tx_hash) {
        toast.success(`${pair} ${side === "long" ? "开多" : "开空"} 指令已发送`);
      } else {
        toast.error("开仓失败，请查看后端日志");
      }
      queryClient.invalidateQueries({ queryKey: ["contract-state"] });
    },
    onError: (e: Error) => toast.error(`开仓失败: ${e.message}`),
  });

  const closeMut = useMutation({
    mutationFn: (p: string) => closeContractPosition({ pair: p }),
    onSuccess: (res, pair) => {
      if (res.tx_hash) {
        toast.success(`${pair} 平仓成功`);
      } else {
        toast.info(`${pair} 平仓指令已发送（dry-run）`);
      }
      queryClient.invalidateQueries({ queryKey: ["contract-state"] });
    },
    onError: (e: Error) => toast.error(`平仓失败: ${e.message}`),
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
                <TableHead className="text-right">操作</TableHead>
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
                  <TableCell className="text-right">
                    <Button
                      variant="destructive"
                      size="sm"
                      disabled={closeMut.isPending}
                      onClick={() => closeMut.mutate(pos.pair)}
                    >
                      {closeMut.isPending ? <Loader2 className="size-3 animate-spin" /> : <X className="size-3" />}
                      平仓
                    </Button>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        ) : (
          <Card>
            <CardContent className="p-8 text-center text-muted-foreground">
              <TrendingUp className="mx-auto mb-3 size-8 opacity-40" />
              <p className="text-sm">当前无合约持仓</p>
              <p className="mt-1 text-xs opacity-60">使用下方开仓面板建立新仓位</p>
            </CardContent>
          </Card>
        )}
      </SectionCard>

      {/* ── 开仓表单 ── */}
      <SectionCard title="开仓" description="选择交易对、方向、投入保证金和杠杆">
        <div className="grid gap-6 md:grid-cols-2">
          {/* 左侧：参数输入 */}
          <div className="space-y-4">
            {/* 交易对 */}
            <div>
              <label className="mb-1.5 block text-xs font-medium text-muted-foreground">交易对</label>
              <select
                className="flex h-10 w-full rounded-xl border border-input bg-background px-3 py-2 text-sm ring-offset-background focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                value={pair}
                onChange={(e) => setPair(e.target.value)}
              >
                {state.pairs.map((p) => (
                  <option key={p} value={p}>{p}</option>
                ))}
              </select>
            </div>

            {/* 杠杆 */}
            <div>
              <label className="mb-1.5 block text-xs font-medium text-muted-foreground">
                杠杆倍数 (1-{pair.startsWith("BTC") || pair.startsWith("ETH") ? state.max_leverage_main : state.max_leverage_alt}x)
              </label>
              <Input
                type="number"
                min={1}
                max={pair.startsWith("BTC") || pair.startsWith("ETH") ? state.max_leverage_main : state.max_leverage_alt}
                value={leverage}
                onChange={(e) => setLeverage(e.target.value)}
              />
            </div>

            {/* 保证金 */}
            <div>
              <label className="mb-1.5 block text-xs font-medium text-muted-foreground">保证金 (USDC)</label>
              <Input
                type="number"
                min={1}
                step={10}
                value={marginUsd}
                onChange={(e) => setMarginUsd(e.target.value)}
              />
            </div>

            {/* 方向按钮 */}
            <div className="flex gap-3 pt-2">
              <Button
                className="flex-1"
                variant={side === "long" ? "default" : "outline"}
                onClick={() => setSide("long")}
              >
                <TrendingUp className="mr-2 size-4" />
                开多
              </Button>
              <Button
                className="flex-1"
                variant={side === "short" ? "default" : "outline"}
                onClick={() => setSide("short")}
              >
                <TrendingUp className="mr-2 size-4 rotate-180" />
                开空
              </Button>
            </div>

            {/* 执行按钮 */}
            <Button
              className="w-full"
              size="lg"
              disabled={openMut.isPending || !marginUsd || parseFloat(marginUsd) <= 0}
              onClick={() => openMut.mutate()}
            >
              {openMut.isPending ? (
                <><Loader2 className="mr-2 size-4 animate-spin" /> 提交中...</>
              ) : (
                <><ArrowLeftRight className="mr-2 size-4" /> {side === "long" ? "开多" : "开空"} {pair}</>
              )}
            </Button>
          </div>

          {/* 右侧：参数预览 */}
          <Card>
            <CardContent className="p-5 space-y-3">
              <div className="text-sm font-medium text-muted-foreground">开仓参数预览</div>
              <div className="space-y-2 text-sm">
                <div className="flex justify-between">
                  <span className="text-muted-foreground">交易对</span>
                  <span className="font-mono font-medium">{pair}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-muted-foreground">方向</span>
                  <span className={cn("font-medium", side === "long" ? "text-[var(--success)]" : "text-[var(--danger)]")}>
                    {side === "long" ? "做多" : "做空"}
                  </span>
                </div>
                <div className="flex justify-between">
                  <span className="text-muted-foreground">杠杆</span>
                  <span className="font-mono font-medium">{leverage || "默认"}x</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-muted-foreground">保证金</span>
                  <span className="font-mono font-medium">{formatUsd(parseFloat(marginUsd) || 0)}</span>
                </div>
                <div className="border-t pt-2 flex justify-between">
                  <span className="text-muted-foreground">名义价值</span>
                  <span className="font-mono font-medium">
                    {formatUsd((parseFloat(marginUsd) || 0) * (parseInt(leverage) || 1))}
                  </span>
                </div>
              </div>
              <div className="rounded-xl bg-[var(--warning-soft)] px-3 py-2 text-xs text-[var(--warning-foreground)]">
                <Check className="mr-1 inline size-3" />
                {state.dry_run ? "模拟模式：不会发送真实链上交易" : "实盘模式：将发送真实链上交易"}
              </div>
            </CardContent>
          </Card>
        </div>
      </SectionCard>
    </div>
  );
}
