import { useQuery } from "@tanstack/react-query";
import { fetchWallet, fetchConfig } from "@/lib/api";
import type { AppConfig, WalletInfo } from "@/types/api";
import { PageHeader, SectionCard, LoadingState, StatusBadge } from "@/components/app-primitives";
import { Badge } from "@/components/ui/badge";

export default function WalletPage() {
  const { data: wallet, isLoading } = useQuery<WalletInfo>({ queryKey: ["wallet"], queryFn: fetchWallet });
  const { data: config } = useQuery<AppConfig>({ queryKey: ["config"], queryFn: fetchConfig });

  if (isLoading) return <LoadingState label="正在加载钱包信息..." />;

  return (
    <div className="space-y-6">
      <PageHeader title="执行钱包" description="用于跟单交易的执行钱包信息" />

      <div className="grid gap-4 md:grid-cols-2">
        <SectionCard title="钱包信息">
          <div className="space-y-4">
            <div>
              <div className="text-xs font-semibold uppercase tracking-[0.12em] text-muted-foreground">地址</div>
              <div className="mt-1 font-mono text-sm break-all">{wallet?.wallet_address || "-"}</div>
            </div>
            <div>
              <div className="text-xs font-semibold uppercase tracking-[0.12em] text-muted-foreground">RPC</div>
              <div className="mt-1 text-sm text-muted-foreground break-all">{wallet?.rpc_http_url || "-"}</div>
            </div>
            <div>
              <div className="text-xs font-semibold uppercase tracking-[0.12em] text-muted-foreground">运行模式</div>
              <div className="mt-1">
                <Badge variant={config?.dry_run ? "warning" : "success"}>{config?.dry_run ? "Dry Run" : "Live"}</Badge>
              </div>
            </div>
          </div>
        </SectionCard>

        <SectionCard title="风控状态">
          <div className="space-y-3">
            <StatusBadge
              ok={!config?.dry_run}
              label="交易执行"
              hint={config?.dry_run ? "当前为 Dry Run 模式，不会发送真实交易" : "实盘模式，正常执行交易"}
            />
            <div className="text-sm text-muted-foreground">
              日亏损上限：<strong>${config?.daily_loss_limit_usd ?? "-"}</strong>
            </div>
          </div>
        </SectionCard>
      </div>

      <SectionCard title="安全提示">
        <ul className="space-y-2 text-sm text-muted-foreground">
          <li className="flex items-center gap-2">
            <span className="flex size-6 shrink-0 items-center justify-center rounded-full bg-[color:var(--warning-soft)] text-[10px] font-bold text-[color:var(--warning-foreground)]">1</span>
            私钥和 API Key 仅存储在服务器 .env 文件中，前端不展示
          </li>
          <li className="flex items-center gap-2">
            <span className="flex size-6 shrink-0 items-center justify-center rounded-full bg-[color:var(--warning-soft)] text-[10px] font-bold text-[color:var(--warning-foreground)]">2</span>
            请勿将此管理页面暴露到公网
          </li>
          <li className="flex items-center gap-2">
            <span className="flex size-6 shrink-0 items-center justify-center rounded-full bg-[color:var(--warning-soft)] text-[10px] font-bold text-[color:var(--warning-foreground)]">3</span>
            建议定期检查交易记录，监控异常活动
          </li>
        </ul>
      </SectionCard>
    </div>
  );
}
