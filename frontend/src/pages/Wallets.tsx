import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Plus, Trash2 } from "lucide-react";
import { toast } from "sonner";
import { fetchConfig, addTarget, deleteTarget } from "@/lib/api";
import type { CopyTarget } from "@/types/api";
import { PageHeader, SectionCard, LoadingState } from "@/components/app-primitives";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Input, Select } from "@/components/ui/input";
import { Dialog, DialogClose, DialogContent, DialogDescription, DialogHeader, DialogTitle, DialogTrigger } from "@/components/ui/dialog";

export default function Wallets() {
  const qc = useQueryClient();
  const { data: config, isLoading } = useQuery({ queryKey: ["config"], queryFn: fetchConfig });
  const [open, setOpen] = useState(false);
  const [address, setAddress] = useState("");
  const [remark, setRemark] = useState("");
  const [mode, setMode] = useState("monitor");

  const addMut = useMutation({
    mutationFn: () => addTarget({ address, remark: remark || undefined, trade_mode: mode }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["config"] });
      setOpen(false);
      setAddress("");
      setRemark("");
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

  const targets = config?.copy_targets ?? [];

  if (isLoading) return <LoadingState label="正在加载钱包列表..." />;

  return (
    <div className="space-y-6">
      <PageHeader
        title="跟单钱包"
        description="管理被监控的链上钱包地址"
        actions={
          <Dialog open={open} onOpenChange={setOpen}>
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
                  <Input
                    value={address}
                    onChange={(e) => setAddress(e.target.value)}
                    placeholder="0x..."
                    className="font-mono"
                  />
                </div>
                <div className="space-y-2">
                  <label className="text-xs font-semibold uppercase tracking-[0.12em] text-muted-foreground">备注</label>
                  <Input
                    value={remark}
                    onChange={(e) => setRemark(e.target.value)}
                    placeholder="可选标识"
                  />
                </div>
                <div className="space-y-2">
                  <label className="text-xs font-semibold uppercase tracking-[0.12em] text-muted-foreground">模式</label>
                  <Select value={mode} onChange={(e) => setMode(e.target.value)}>
                    <option value="monitor">监测</option>
                    <option value="ratio">比例跟单</option>
                    <option value="fixed">固定跟单</option>
                  </Select>
                </div>
                <div className="flex justify-end gap-3 pt-2">
                  <DialogClose asChild>
                    <Button variant="secondary">取消</Button>
                  </DialogClose>
                  <Button onClick={() => addMut.mutate()} disabled={!address || addMut.isPending}>
                    {addMut.isPending ? "添加中..." : "添加"}
                  </Button>
                </div>
              </div>
            </DialogContent>
          </Dialog>
        }
      />

      <SectionCard title="目标地址" description={targets.length > 0 ? `共 ${targets.length} 个地址` : undefined}>
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>地址</TableHead>
              <TableHead>备注</TableHead>
              <TableHead>模式</TableHead>
              <TableHead>比例</TableHead>
              <TableHead>固定金额</TableHead>
              <TableHead className="w-16">操作</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {targets.length === 0 ? (
              <TableRow>
                <TableCell colSpan={6} className="text-center py-8 text-muted-foreground">
                  暂无跟单钱包，点击右上角添加
                </TableCell>
              </TableRow>
            ) : (
              targets.map((t: CopyTarget) => (
                <TableRow key={t.address}>
                  <TableCell className="font-mono text-xs">
                    {t.address.slice(0, 12)}...{t.address.slice(-6)}
                  </TableCell>
                  <TableCell>{t.remark || "-"}</TableCell>
                  <TableCell>
                    <Badge variant={t.trade_mode === "monitor" ? "warning" : "success"}>
                      {t.trade_mode || config?.trade_mode || "-"}
                    </Badge>
                  </TableCell>
                  <TableCell>{t.trade_ratio != null ? `${(t.trade_ratio * 100).toFixed(0)}%` : "-"}</TableCell>
                  <TableCell>{t.trade_fixed_usd ? `$${t.trade_fixed_usd}` : "-"}</TableCell>
                  <TableCell>
                    <Button
                      variant="ghost"
                      size="icon"
                      onClick={() => {
                        if (confirm("确认删除此目标？")) delMut.mutate(t.address);
                      }}
                    >
                      <Trash2 className="size-4 text-[var(--danger)]" />
                    </Button>
                  </TableCell>
                </TableRow>
              ))
            )}
          </TableBody>
        </Table>
      </SectionCard>
    </div>
  );
}
