import { useState, useEffect, type ChangeEvent } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { fetchConfig, updateParams } from "@/lib/api";
import type { AppConfig } from "@/types/api";
import { PageHeader, SectionCard, LoadingState } from "@/components/app-primitives";
import { Button } from "@/components/ui/button";
import { Input, Select } from "@/components/ui/input";

export default function Params() {
  const qc = useQueryClient();
  const { data: config, isLoading } = useQuery<AppConfig>({ queryKey: ["config"], queryFn: fetchConfig });
  const [form, setForm] = useState<Record<string, any>>({});
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (config) {
      setForm({
        base_token: config.base_token,
        trade_mode: config.trade_mode,
        trade_ratio: config.trade_ratio,
        trade_fixed_usd: config.trade_fixed_usd,
        trade_max_usd: config.trade_max_usd,
        trade_fixed_virtuals: config.trade_fixed_virtuals,
        slippage: config.slippage,
        gas_limit_gwei: config.gas_limit_gwei,
        daily_loss_limit_usd: config.daily_loss_limit_usd,
        take_profit_roi: config.take_profit_roi,
        take_profit_check_sec: config.take_profit_check_sec,
        poll_interval_sec: config.poll_interval_sec,
        min_trade_usd: config.min_trade_usd,
        dry_run: config.dry_run,
      });
    }
  }, [config]);

  const handleSave = async () => {
    setSaving(true);
    try {
      await updateParams(form);
      qc.invalidateQueries({ queryKey: ["config"] });
      toast.success("参数保存成功，bot 将在 60 秒内自动生效");
    } catch (e: any) {
      toast.error(`保存失败: ${e.message}`);
    }
    setSaving(false);
  };

  if (isLoading) return <LoadingState label="正在加载参数..." />;

  const setNum = (key: string) => (e: ChangeEvent<HTMLInputElement>) =>
    setForm({ ...form, [key]: parseFloat(e.target.value) || 0 });

  const setStr = (key: string) => (e: ChangeEvent<HTMLSelectElement>) =>
    setForm({ ...form, [key]: e.target.value });

  return (
    <div className="space-y-6">
      <PageHeader title="跟单参数" description="全局跟单策略参数配置" />

      <SectionCard title="全局参数" description="修改后 bot 在 60 秒内自动热加载">
        <div className="grid gap-5 sm:grid-cols-2">
          <Field label="基础代币">
            <Select value={form.base_token} onChange={setStr("base_token")}>
              <option value="VIRTUAL">VIRTUAL</option>
              <option value="USDC">USDC</option>
            </Select>
          </Field>
          <Field label="跟单模式">
            <Select value={form.trade_mode} onChange={setStr("trade_mode")}>
              <option value="ratio">ratio（比例）</option>
              <option value="fixed">fixed（固定）</option>
              <option value="monitor">monitor（监测）</option>
            </Select>
          </Field>
          <Field label="跟单比例">
            <Input type="number" step="0.01" value={form.trade_ratio ?? ""} onChange={setNum("trade_ratio")} />
          </Field>
          <Field label="固定金额 (USDC)">
            <Input type="number" value={form.trade_fixed_usd ?? ""} onChange={setNum("trade_fixed_usd")} />
          </Field>
          <Field label="单笔上限 (USDC)">
            <Input type="number" value={form.trade_max_usd ?? ""} onChange={setNum("trade_max_usd")} />
          </Field>
          <Field label="固定 VIRTUAL 数量">
            <Input type="number" value={form.trade_fixed_virtuals ?? ""} onChange={setNum("trade_fixed_virtuals")} />
          </Field>
          <Field label="最大滑点">
            <Input type="number" step="0.001" value={form.slippage ?? ""} onChange={setNum("slippage")} />
          </Field>
          <Field label="Gas 上限 (gwei)">
            <Input type="number" value={form.gas_limit_gwei ?? ""} onChange={setNum("gas_limit_gwei")} />
          </Field>
          <Field label="日亏损上限 (USDC)">
            <Input type="number" value={form.daily_loss_limit_usd ?? ""} onChange={setNum("daily_loss_limit_usd")} />
          </Field>
          <Field label="止盈 ROI">
            <Input type="number" step="0.01" value={form.take_profit_roi ?? ""} onChange={setNum("take_profit_roi")} />
          </Field>
          <Field label="轮询间隔 (秒)">
            <Input type="number" value={form.poll_interval_sec ?? ""} onChange={setNum("poll_interval_sec")} />
          </Field>
          <Field label="最小跟单额 (USDC)">
            <Input type="number" value={form.min_trade_usd ?? ""} onChange={setNum("min_trade_usd")} />
          </Field>
          <Field label="Dry Run">
            <Select value={String(form.dry_run)} onChange={(e) => setForm({ ...form, dry_run: e.target.value === "true" })}>
              <option value="false">false（实盘）</option>
              <option value="true">true（模拟）</option>
            </Select>
          </Field>
        </div>
        <div className="mt-6 flex items-center gap-3">
          <Button onClick={handleSave} disabled={saving}>
            {saving ? "保存中..." : "保存参数"}
          </Button>
        </div>
      </SectionCard>
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="space-y-2">
      <label className="text-xs font-semibold uppercase tracking-[0.12em] text-muted-foreground">{label}</label>
      {children}
    </div>
  );
}
