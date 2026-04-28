import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Eye, EyeOff, Pencil } from "lucide-react";
import { fetchWallet, fetchConfig, updateWallet } from "@/lib/api";
import type { WalletInfo, AppConfig } from "@/types/api";
import { PageHeader, SectionCard, LoadingState, StatusBadge } from "@/components/app-primitives";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Dialog, DialogClose, DialogContent, DialogHeader, DialogTitle, DialogDescription } from "@/components/ui/dialog";

export default function WalletPage() {
  const qc = useQueryClient();
  const { data: wallet, isLoading } = useQuery<WalletInfo>({ queryKey: ["wallet"], queryFn: fetchWallet });
  const { data: config } = useQuery<AppConfig>({ queryKey: ["config"], queryFn: fetchConfig });

  const [editOpen, setEditOpen] = useState(false);
  const [showSecrets, setShowSecrets] = useState(false);
  const [form, setForm] = useState({
    wallet_address: "",
    rpc_http_url: "",
    rpc_ws_url: "",
    private_key: "",
    okx_api_key: "",
    okx_secret_key: "",
    okx_passphrase: "",
  });

  const editMut = useMutation({
    mutationFn: () => {
      const data: Record<string, string> = {};
      if (form.wallet_address !== wallet?.wallet_address && form.wallet_address) data.wallet_address = form.wallet_address;
      if (form.rpc_http_url !== wallet?.rpc_http_url && form.rpc_http_url) data.rpc_http_url = form.rpc_http_url;
      if (form.rpc_ws_url !== (wallet as any)?.rpc_ws_url && form.rpc_ws_url) data.rpc_ws_url = form.rpc_ws_url;
      if (form.private_key) data.private_key = form.private_key;
      if (form.okx_api_key) data.okx_api_key = form.okx_api_key;
      if (form.okx_secret_key) data.okx_secret_key = form.okx_secret_key;
      if (form.okx_passphrase) data.okx_passphrase = form.okx_passphrase;
      return updateWallet(data);
    },
    onSuccess: (res) => {
      qc.invalidateQueries({ queryKey: ["wallet"] });
      setEditOpen(false);
      setForm({
        wallet_address: "", rpc_http_url: "", rpc_ws_url: "",
        private_key: "", okx_api_key: "", okx_secret_key: "", okx_passphrase: "",
      });
      toast.success(`已更新: ${res.updated.join(", ")}`);
    },
    onError: (e: Error) => toast.error(`更新失败: ${e.message}`),
  });

  const openEdit = () => {
    setForm({
      wallet_address: wallet?.wallet_address ?? "",
      rpc_http_url: wallet?.rpc_http_url ?? "",
      rpc_ws_url: (wallet as any)?.rpc_ws_url ?? "",
      private_key: "",
      okx_api_key: "",
      okx_secret_key: "",
      okx_passphrase: "",
    });
    setShowSecrets(false);
    setEditOpen(true);
  };

  if (isLoading) return <LoadingState label="正在加载钱包信息..." />;

  return (
    <div className="space-y-6">
      <PageHeader
        title="执行钱包"
        description="用于跟单交易的执行钱包信息"
        actions={
          <Button onClick={openEdit}>
            <Pencil className="size-4" />
            编辑
          </Button>
        }
      />

      <div className="grid gap-4 md:grid-cols-2">
        <SectionCard title="钱包信息">
          <div className="space-y-4">
            <div>
              <div className="text-xs font-semibold uppercase tracking-[0.12em] text-muted-foreground">地址</div>
              <div className="mt-1 font-mono text-sm break-all">{wallet?.wallet_address || "-"}</div>
            </div>
            <div>
              <div className="text-xs font-semibold uppercase tracking-[0.12em] text-muted-foreground">RPC HTTP</div>
              <div className="mt-1 text-sm text-muted-foreground break-all">{wallet?.rpc_http_url || "-"}</div>
            </div>
            <div>
              <div className="text-xs font-semibold uppercase tracking-[0.12em] text-muted-foreground">RPC WS</div>
              <div className="mt-1 text-sm text-muted-foreground break-all">{(wallet as any)?.rpc_ws_url || "-"}</div>
            </div>
            <div>
              <div className="text-xs font-semibold uppercase tracking-[0.12em] text-muted-foreground">运行模式</div>
              <div className="mt-1">
                <Badge variant={config?.dry_run ? "warning" : "success"}>{config?.dry_run ? "Dry Run" : "Live"}</Badge>
              </div>
            </div>
            <div className="grid grid-cols-2 gap-4 pt-2 text-sm text-muted-foreground">
              <div>私钥已配置: <strong>{wallet?.has_private_key ? "✓" : "✗"}</strong></div>
              <div>OKX API 已配置: <strong>{wallet?.has_okx_api_key ? "✓" : "✗"}</strong></div>
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
            编辑钱包/API 信息后需重启 bot 服务才能生效
          </li>
          <li className="flex items-center gap-2">
            <span className="flex size-6 shrink-0 items-center justify-center rounded-full bg-[color:var(--warning-soft)] text-[10px] font-bold text-[color:var(--warning-foreground)]">2</span>
            私钥和 API Key 以密文存储，更改时需重新输入完整值
          </li>
          <li className="flex items-center gap-2">
            <span className="flex size-6 shrink-0 items-center justify-center rounded-full bg-[color:var(--warning-soft)] text-[10px] font-bold text-[color:var(--warning-foreground)]">3</span>
            请勿将此管理页面暴露到公网
          </li>
        </ul>
      </SectionCard>

      {/* Edit Dialog */}
      <Dialog open={editOpen} onOpenChange={setEditOpen}>
        <DialogContent className="max-w-xl">
          <DialogHeader>
            <DialogTitle>编辑执行钱包</DialogTitle>
            <DialogDescription>修改后需重启 bot 服务才能生效（systemctl restart auto-trader）</DialogDescription>
          </DialogHeader>
          <div className="space-y-4 max-h-[60vh] overflow-y-auto pr-2">
            <div className="space-y-2">
              <label className="text-xs font-semibold uppercase tracking-[0.12em] text-muted-foreground">钱包地址</label>
              <Input value={form.wallet_address} onChange={(e) => setForm({ ...form, wallet_address: e.target.value })} className="font-mono" />
            </div>
            <div className="space-y-2">
              <label className="text-xs font-semibold uppercase tracking-[0.12em] text-muted-foreground">RPC HTTP URL</label>
              <Input value={form.rpc_http_url} onChange={(e) => setForm({ ...form, rpc_http_url: e.target.value })} className="font-mono text-xs" />
            </div>
            <div className="space-y-2">
              <label className="text-xs font-semibold uppercase tracking-[0.12em] text-muted-foreground">RPC WS URL</label>
              <Input value={form.rpc_ws_url} onChange={(e) => setForm({ ...form, rpc_ws_url: e.target.value })} className="font-mono text-xs" />
            </div>

            <div className="flex items-center gap-2 pt-2">
              <Button variant="secondary" size="sm" onClick={() => setShowSecrets(!showSecrets)}>
                {showSecrets ? <EyeOff className="size-4" /> : <Eye className="size-4" />}
                {showSecrets ? "隐藏密钥字段" : "修改密钥"}
              </Button>
              <span className="text-xs text-muted-foreground">不修改则留空</span>
            </div>

            {showSecrets && (
              <>
                <div className="space-y-2">
                  <label className="text-xs font-semibold uppercase tracking-[0.12em] text-muted-foreground">私钥 (PRIVATE_KEY)</label>
                  <Input type="password" value={form.private_key} onChange={(e) => setForm({ ...form, private_key: e.target.value })} placeholder="0x..." className="font-mono" />
                </div>
                <div className="space-y-2">
                  <label className="text-xs font-semibold uppercase tracking-[0.12em] text-muted-foreground">OKX API Key</label>
                  <Input type="password" value={form.okx_api_key} onChange={(e) => setForm({ ...form, okx_api_key: e.target.value })} />
                </div>
                <div className="space-y-2">
                  <label className="text-xs font-semibold uppercase tracking-[0.12em] text-muted-foreground">OKX Secret Key</label>
                  <Input type="password" value={form.okx_secret_key} onChange={(e) => setForm({ ...form, okx_secret_key: e.target.value })} />
                </div>
                <div className="space-y-2">
                  <label className="text-xs font-semibold uppercase tracking-[0.12em] text-muted-foreground">OKX Passphrase</label>
                  <Input type="password" value={form.okx_passphrase} onChange={(e) => setForm({ ...form, okx_passphrase: e.target.value })} />
                </div>
              </>
            )}
          </div>
          <div className="flex justify-end gap-3 pt-4">
            <DialogClose asChild><Button variant="secondary">取消</Button></DialogClose>
            <Button onClick={() => editMut.mutate()} disabled={editMut.isPending}>
              {editMut.isPending ? "保存中..." : "保存"}
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
