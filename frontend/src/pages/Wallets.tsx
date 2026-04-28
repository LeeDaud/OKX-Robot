import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Plus, Trash2, Pencil, Info } from "lucide-react";
import { toast } from "sonner";
import { fetchConfig, addTarget, deleteTarget, updateTarget } from "@/lib/api";
import type { CopyTarget } from "@/types/api";
import { PageHeader, SectionCard, LoadingState } from "@/components/app-primitives";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Input, Select } from "@/components/ui/input";
import { Dialog, DialogClose, DialogContent, DialogDescription, DialogHeader, DialogTitle, DialogTrigger } from "@/components/ui/dialog";

const MODE_OPTIONS = [
  { value: "monitor", label: "监测" },
  { value: "ratio", label: "比例跟单" },
  { value: "fixed", label: "固定跟单" },
];

function modeLabel(mode?: string): string {
  if (mode === "ratio") return "比例跟单";
  if (mode === "fixed") return "固定跟单";
  if (mode === "monitor") return "仅监测";
  return mode ?? "-";
}

export default function Wallets() {
  const qc = useQueryClient();
  const { data: config, isLoading } = useQuery({ queryKey: ["config"], queryFn: fetchConfig });

  // Add dialog state
  const [addOpen, setAddOpen] = useState(false);
  const [address, setAddress] = useState("");
  const [remark, setRemark] = useState("");
  const [mode, setMode] = useState("monitor");
  const [ratio, setRatio] = useState("");
  const [fixedUsd, setFixedUsd] = useState("");
  const [fixedVirtuals, setFixedVirtuals] = useState("");

  // Edit dialog state
  const [editOpen, setEditOpen] = useState(false);
  const [editTarget, setEditTarget] = useState<CopyTarget | null>(null);
  const [editRemark, setEditRemark] = useState("");
  const [editMode, setEditMode] = useState("");
  const [editRatio, setEditRatio] = useState("");
  const [editFixedUsd, setEditFixedUsd] = useState("");
  const [editFixedVirtuals, setEditFixedVirtuals] = useState("");

  const addMut = useMutation({
    mutationFn: () =>
      addTarget({
        address,
        remark: remark || undefined,
        trade_mode: mode,
        trade_ratio: mode === "ratio" && ratio ? parseFloat(ratio) : undefined,
        trade_fixed_usd: mode === "fixed" && fixedUsd ? parseFloat(fixedUsd) : undefined,
        trade_fixed_virtuals: mode === "fixed" && fixedVirtuals ? parseFloat(fixedVirtuals) : undefined,
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["config"] });
      setAddOpen(false);
      setAddress("");
      setRemark("");
      setMode("monitor");
      setRatio("");
      setFixedUsd("");
      setFixedVirtuals("");
      toast.success("跟单钱包已添加");
    },
    onError: (e: Error) => toast.error(`添加失败: ${e.message}`),
  });

  const delMut = useMutation({
    mutationFn: (addr: string) => deleteTarget(addr),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["config"] });
      toast.success("已删除目标");
    },
    onError: (e: Error) => toast.error(`删除失败: ${e.message}`),
  });

  const editMut = useMutation({
    mutationFn: (params: { address: string; data: Partial<CopyTarget> }) =>
      updateTarget(params.address, params.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["config"] });
      setEditOpen(false);
      toast.success("已更新");
    },
    onError: (e: Error) => toast.error(`更新失败: ${e.message}`),
  });

  const openEdit = (t: CopyTarget) => {
    setEditTarget(t);
    setEditRemark(t.remark ?? "");
    setEditMode(t.trade_mode ?? config?.trade_mode ?? "monitor");
    setEditRatio(t.trade_ratio != null ? String(t.trade_ratio) : "");
    setEditFixedUsd(t.trade_fixed_usd != null ? String(t.trade_fixed_usd) : "");
    setEditFixedVirtuals(t.trade_fixed_virtuals != null ? String(t.trade_fixed_virtuals) : "");
    setEditOpen(true);
  };

  const doEdit = () => {
    if (!editTarget) return;
    const data: Partial<CopyTarget> = {};
    if (editRemark !== (editTarget.remark ?? "")) data.remark = editRemark || undefined;
    if (editMode !== (editTarget.trade_mode ?? config?.trade_mode)) data.trade_mode = editMode;
    const r = editRatio ? parseFloat(editRatio) : undefined;
    if (r !== editTarget.trade_ratio) data.trade_ratio = r;
    const f = editFixedUsd ? parseFloat(editFixedUsd) : undefined;
    if (f !== editTarget.trade_fixed_usd) data.trade_fixed_usd = f;
    const v = editFixedVirtuals ? parseFloat(editFixedVirtuals) : undefined;
    if (v !== editTarget.trade_fixed_virtuals) data.trade_fixed_virtuals = v;
    editMut.mutate({ address: editTarget.address, data });
  };

  const targets = config?.copy_targets ?? [];
  const globalMode = config?.trade_mode ?? "";
  const isGlobalMonitor = globalMode === "monitor";

  if (isLoading) return <LoadingState label="正在加载钱包列表..." />;

  return (
    <div className="space-y-6">
      <PageHeader
        title="跟单钱包"
        description="管理被监控的链上钱包地址"
        actions={
          <Dialog open={addOpen} onOpenChange={setAddOpen}>
            <DialogTrigger asChild>
              <Button>
                <Plus className="size-4" />
                添加钱包
              </Button>
            </DialogTrigger>
            <DialogContent>
              <DialogHeader>
                <DialogTitle>添加跟单钱包</DialogTitle>
                <DialogDescription>输入目标钱包地址及相关配置</DialogDescription>
              </DialogHeader>
              <div className="space-y-4">
                <div className="space-y-2">
                  <label className="text-xs font-semibold uppercase tracking-[0.12em] text-muted-foreground">地址</label>
                  <Input value={address} onChange={(e) => setAddress(e.target.value)} placeholder="0x..." className="font-mono" />
                </div>
                <div className="space-y-2">
                  <label className="text-xs font-semibold uppercase tracking-[0.12em] text-muted-foreground">备注</label>
                  <Input value={remark} onChange={(e) => setRemark(e.target.value)} placeholder="可选标识" />
                </div>
                <div className="space-y-2">
                  <label className="text-xs font-semibold uppercase tracking-[0.12em] text-muted-foreground">跟单模式</label>
                  <Select value={mode} onChange={(e) => setMode(e.target.value)}>
                    {MODE_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
                  </Select>
                </div>
                {mode !== "monitor" && (
                  <>
                    {mode === "ratio" && (
                      <div className="space-y-2">
                        <label className="text-xs font-semibold uppercase tracking-[0.12em] text-muted-foreground">跟单比例</label>
                        <Input type="number" step="0.01" value={ratio} onChange={(e) => setRatio(e.target.value)} placeholder="0.5" />
                      </div>
                    )}
                    {mode === "fixed" && (
                      <>
                        <div className="space-y-2">
                          <label className="text-xs font-semibold uppercase tracking-[0.12em] text-muted-foreground">固定金额 (USDC)</label>
                          <Input type="number" value={fixedUsd} onChange={(e) => setFixedUsd(e.target.value)} placeholder="50" />
                        </div>
                        <div className="space-y-2">
                          <label className="text-xs font-semibold uppercase tracking-[0.12em] text-muted-foreground">固定 VIRTUAL 数量</label>
                          <Input type="number" value={fixedVirtuals} onChange={(e) => setFixedVirtuals(e.target.value)} placeholder="30" />
                        </div>
                      </>
                    )}
                  </>
                )}
                <div className="flex justify-end gap-3 pt-2">
                  <DialogClose asChild><Button variant="secondary">取消</Button></DialogClose>
                  <Button onClick={() => addMut.mutate()} disabled={!address || addMut.isPending}>
                    {addMut.isPending ? "添加中..." : "添加"}
                  </Button>
                </div>
              </div>
            </DialogContent>
          </Dialog>
        }
      />

      {/* 全局跟单状态 */}
      <div className={`flex items-center gap-3 rounded-[16px] border p-4 ${isGlobalMonitor ? "border-[var(--warning)]/30 bg-[var(--warning-soft)]" : "border-[var(--success)]/30 bg-[var(--success-soft)]"}`}>
        <Info className={`size-5 ${isGlobalMonitor ? "text-[var(--warning-foreground)]" : "text-[var(--success-foreground)]"}`} />
        <div className="text-sm">
          <span className="font-semibold">全局模式：</span>
          <Badge variant={isGlobalMonitor ? "warning" : "success"}>{modeLabel(globalMode)}</Badge>
          {isGlobalMonitor ? (
            <span className="ml-2 text-[var(--warning-foreground)]">所有目标均不执行跟单，仅监测交易</span>
          ) : (
            <span className="ml-2 text-[var(--success-foreground)]">跟单已启用</span>
          )}
        </div>
      </div>

      <SectionCard title="目标地址" description={targets.length > 0 ? `共 ${targets.length} 个地址` : undefined}>
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>地址</TableHead>
              <TableHead>备注</TableHead>
              <TableHead>模式</TableHead>
              <TableHead>比例</TableHead>
              <TableHead>固定 USDC</TableHead>
              <TableHead>固定 VIRTUAL</TableHead>
              <TableHead className="w-24">操作</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {targets.length === 0 ? (
              <TableRow>
                <TableCell colSpan={7} className="text-center py-8 text-muted-foreground">
                  暂无跟单钱包，点击右上角添加
                </TableCell>
              </TableRow>
            ) : (
              targets.map((t: CopyTarget) => {
                const effectiveMode = t.trade_mode || globalMode;
                const isOverridden = !!t.trade_mode && t.trade_mode !== globalMode;
                return (
                  <TableRow key={t.address}>
                    <TableCell className="font-mono text-xs">{t.address.slice(0, 12)}...{t.address.slice(-6)}</TableCell>
                    <TableCell>{t.remark || "-"}</TableCell>
                    <TableCell>
                      <Badge variant={effectiveMode === "monitor" ? "warning" : "success"}>
                        {modeLabel(effectiveMode)}
                      </Badge>
                      {isOverridden && (
                        <span className="ml-1.5 text-[10px] text-muted-foreground">独立</span>
                      )}
                    </TableCell>
                    <TableCell>{t.trade_ratio != null ? `${(t.trade_ratio * 100).toFixed(0)}%` : "-"}</TableCell>
                    <TableCell>{t.trade_fixed_usd ? `$${t.trade_fixed_usd}` : "-"}</TableCell>
                    <TableCell>{t.trade_fixed_virtuals ?? "-"}</TableCell>
                    <TableCell>
                      <div className="flex items-center gap-1">
                        <Button variant="ghost" size="icon" onClick={() => openEdit(t)}>
                          <Pencil className="size-4" />
                        </Button>
                        <Button variant="ghost" size="icon" onClick={() => { if (confirm("确认删除此目标？")) delMut.mutate(t.address); }}>
                          <Trash2 className="size-4 text-[var(--danger)]" />
                        </Button>
                      </div>
                    </TableCell>
                  </TableRow>
                );
              })
            )}
          </TableBody>
        </Table>
      </SectionCard>

      {/* Edit Dialog */}
      <Dialog open={editOpen} onOpenChange={setEditOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>编辑跟单钱包</DialogTitle>
            <DialogDescription>{editTarget?.address ? `${editTarget.address.slice(0, 12)}...${editTarget.address.slice(-6)}` : ""}</DialogDescription>
          </DialogHeader>
          {editTarget && (
            <div className="space-y-4">
              <div className="space-y-2">
                <label className="text-xs font-semibold uppercase tracking-[0.12em] text-muted-foreground">备注</label>
                <Input value={editRemark} onChange={(e) => setEditRemark(e.target.value)} placeholder="可选标识" />
              </div>
              <div className="space-y-2">
                <label className="text-xs font-semibold uppercase tracking-[0.12em] text-muted-foreground">跟单模式</label>
                <Select value={editMode} onChange={(e) => setEditMode(e.target.value)}>
                  {MODE_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
                </Select>
                {editMode === (config?.trade_mode ?? "monitor") && (
                  <p className="text-xs text-muted-foreground">当前使用全局模式设置</p>
                )}
                {editMode !== (config?.trade_mode ?? "monitor") && editMode !== "monitor" && (
                  <p className="text-xs text-[var(--warning-foreground)]">独立模式，覆盖全局设置</p>
                )}
              </div>
              {editMode !== "monitor" && (
                <>
                  {editMode === "ratio" && (
                    <div className="space-y-2">
                      <label className="text-xs font-semibold uppercase tracking-[0.12em] text-muted-foreground">跟单比例</label>
                      <Input type="number" step="0.01" value={editRatio} onChange={(e) => setEditRatio(e.target.value)} placeholder="0.5" />
                    </div>
                  )}
                  {editMode === "fixed" && (
                    <>
                      <div className="space-y-2">
                        <label className="text-xs font-semibold uppercase tracking-[0.12em] text-muted-foreground">固定金额 (USDC)</label>
                        <Input type="number" value={editFixedUsd} onChange={(e) => setEditFixedUsd(e.target.value)} placeholder="50" />
                      </div>
                      <div className="space-y-2">
                        <label className="text-xs font-semibold uppercase tracking-[0.12em] text-muted-foreground">固定 VIRTUAL 数量</label>
                        <Input type="number" value={editFixedVirtuals} onChange={(e) => setEditFixedVirtuals(e.target.value)} placeholder="30" />
                      </div>
                    </>
                  )}
                </>
              )}
              <div className="flex justify-end gap-3 pt-2">
                <DialogClose asChild><Button variant="secondary">取消</Button></DialogClose>
                <Button onClick={doEdit} disabled={editMut.isPending}>
                  {editMut.isPending ? "保存中..." : "保存"}
                </Button>
              </div>
            </div>
          )}
        </DialogContent>
      </Dialog>
    </div>
  );
}
