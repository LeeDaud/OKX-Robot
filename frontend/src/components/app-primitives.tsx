import type { ReactNode } from "react";
import { AlertCircle, ArrowRight, CheckCircle2, Clock3, LoaderCircle, ShieldAlert } from "lucide-react";
import { Link } from "react-router-dom";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";

export function PageHeader({
  eyebrow,
  title,
  description,
  actions,
}: {
  eyebrow?: string;
  title: string;
  description: string;
  actions?: ReactNode;
}) {
  return (
    <div className="flex flex-col gap-5 lg:flex-row lg:items-end lg:justify-between">
      <div className="space-y-2">
        {eyebrow ? (
          <div className="text-xs font-semibold uppercase tracking-[0.24em] text-primary/80">{eyebrow}</div>
        ) : null}
        <div className="space-y-2">
          <h1 className="text-3xl font-semibold tracking-[-0.04em] text-balance md:text-4xl">{title}</h1>
          <p className="max-w-3xl text-sm leading-6 text-muted-foreground md:text-base">{description}</p>
        </div>
      </div>
      {actions ? <div className="flex flex-wrap gap-3">{actions}</div> : null}
    </div>
  );
}

export function MetricCard({
  label,
  value,
  hint,
  tone = "default",
}: {
  label: string;
  value: string;
  hint: string;
  tone?: "default" | "success" | "warning" | "danger";
}) {
  const accentClass =
    tone === "success"
      ? "from-[color:var(--metric-success-from)] to-[color:var(--metric-success-to)] text-[color:var(--metric-success-text)]"
      : tone === "warning"
        ? "from-[color:var(--metric-warning-from)] to-[color:var(--metric-warning-to)] text-[color:var(--metric-warning-text)]"
        : tone === "danger"
          ? "from-[color:var(--metric-danger-from)] to-[color:var(--metric-danger-to)] text-[color:var(--metric-danger-text)]"
          : "from-primary/14 to-[color:var(--surface-soft-strong)] text-foreground";

  return (
    <Card className={cn("overflow-hidden", tone === "default" ? "" : "border-transparent")}>
      <CardContent className={cn("bg-gradient-to-br px-5 py-5", accentClass)}>
        <div className="text-xs font-semibold uppercase tracking-[0.18em] text-muted-foreground">{label}</div>
        <div className="mt-3 text-3xl font-semibold tracking-[-0.04em]">{value}</div>
        <div className="mt-2 text-sm text-muted-foreground">{hint}</div>
      </CardContent>
    </Card>
  );
}

export function SectionCard({
  title,
  description,
  actions,
  children,
  className,
}: {
  title: string;
  description?: string;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <Card className={className}>
      <CardHeader className="flex-row items-start justify-between gap-4">
        <div className="space-y-1">
          <CardTitle>{title}</CardTitle>
          {description ? <CardDescription>{description}</CardDescription> : null}
        </div>
        {actions ? <div className="flex flex-wrap gap-2">{actions}</div> : null}
      </CardHeader>
      <CardContent>{children}</CardContent>
    </Card>
  );
}

export function StatusBadge({ ok, label, hint }: { ok: boolean; label: string; hint: string }) {
  return (
    <div className="rounded-[22px] border border-border bg-[color:var(--surface-soft)] p-4">
      <div className="flex items-center gap-3">
        <span
          className={cn(
            "flex size-10 items-center justify-center rounded-full",
            ok
              ? "bg-[color:var(--success-soft)] text-[color:var(--success-foreground)]"
              : "bg-[color:var(--danger-soft)] text-[color:var(--danger-foreground)]",
          )}
        >
          {ok ? <CheckCircle2 className="size-4" /> : <ShieldAlert className="size-4" />}
        </span>
        <div className="space-y-1">
          <div className="flex items-center gap-2">
            <div className="font-medium">{label}</div>
            <Badge variant={ok ? "success" : "danger"}>{ok ? "正常" : "关注"}</Badge>
          </div>
          <div className="text-sm text-muted-foreground">{hint}</div>
        </div>
      </div>
    </div>
  );
}

export function EmptyState({
  title,
  description,
  action,
  compact = false,
}: {
  title: string;
  description: string;
  action?: ReactNode;
  compact?: boolean;
}) {
  return (
    <div
      className={cn(
        "rounded-[26px] border border-dashed border-border surface-empty px-6 py-8 text-center",
        compact && "py-6",
      )}
    >
      <div className="mx-auto flex size-12 items-center justify-center rounded-full surface-contrast text-primary shadow-sm">
        <AlertCircle className="size-5" />
      </div>
      <h3 className="mt-4 text-lg font-semibold tracking-[-0.02em]">{title}</h3>
      <p className="mx-auto mt-2 max-w-xl text-sm leading-6 text-muted-foreground">{description}</p>
      {action ? <div className="mt-5 flex justify-center">{action}</div> : null}
    </div>
  );
}

export function LoadingState({ label = "正在加载..." }: { label?: string }) {
  return (
    <div className="space-y-4">
      <div className="flex items-center gap-3 text-sm text-muted-foreground">
        <LoaderCircle className="size-4 animate-spin" />
        <span>{label}</span>
      </div>
      <div className="grid gap-4 md:grid-cols-3">
        <Skeleton className="h-32" />
        <Skeleton className="h-32" />
        <Skeleton className="h-32" />
      </div>
      <Skeleton className="h-72" />
    </div>
  );
}

export function QuickLink({ to, title, description }: { to: string; title: string; description: string }) {
  return (
    <Link
      to={to}
      className="group flex items-center justify-between rounded-[22px] border border-border bg-[color:var(--surface-soft)] px-4 py-4 transition hover:-translate-y-0.5 hover:bg-[color:var(--surface-soft-strong)]"
    >
      <div>
        <div className="font-medium">{title}</div>
        <div className="mt-1 text-sm text-muted-foreground">{description}</div>
      </div>
      <ArrowRight className="size-4 text-muted-foreground transition group-hover:text-primary" />
    </Link>
  );
}

export function DataHint({ icon = <Clock3 className="size-4" />, children }: { icon?: ReactNode; children: ReactNode }) {
  return (
    <div className="flex items-center gap-2 text-sm text-muted-foreground">
      <span className="text-primary">{icon}</span>
      <span>{children}</span>
    </div>
  );
}
