import { cva, type VariantProps } from "class-variance-authority";
import * as React from "react";
import { cn } from "@/lib/utils";

const badgeVariants = cva(
  "inline-flex items-center rounded-full px-3 py-1 text-xs font-semibold tracking-[0.02em]",
  {
    variants: {
      variant: {
        default: "bg-primary/12 text-primary ring-1 ring-primary/15",
        secondary: "bg-muted text-muted-foreground ring-1 ring-border",
        success:
          "bg-[color:var(--success-soft)] text-[color:var(--success-foreground)] ring-1 ring-[color:var(--success-soft)]",
        warning:
          "bg-[color:var(--warning-soft)] text-[color:var(--warning-foreground)] ring-1 ring-[color:var(--warning-soft)]",
        danger:
          "bg-[color:var(--danger-soft)] text-[color:var(--danger-foreground)] ring-1 ring-[color:var(--danger-soft)]",
      },
    },
    defaultVariants: {
      variant: "default",
    },
  },
);

export interface BadgeProps
  extends React.HTMLAttributes<HTMLDivElement>,
    VariantProps<typeof badgeVariants> {}

export function Badge({ className, variant, ...props }: BadgeProps) {
  return <div className={cn(badgeVariants({ variant }), className)} {...props} />;
}
