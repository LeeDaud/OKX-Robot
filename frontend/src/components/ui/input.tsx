import * as React from "react";
import { cn } from "@/lib/utils";

export const inputBaseStyle =
  "flex w-full rounded-full border border-input bg-[color:var(--surface-soft)] px-4 py-2 text-sm ring-offset-background placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-55";

export function Input({ className, type, ...props }: React.ComponentProps<"input">) {
  return (
    <input
      type={type}
      className={cn(inputBaseStyle, "h-10", className)}
      {...props}
    />
  );
}

export function Select({ className, ...props }: React.ComponentProps<"select">) {
  return (
    <select
      className={cn(inputBaseStyle, "h-10 appearance-none", className)}
      {...props}
    />
  );
}

export function Textarea({ className, ...props }: React.ComponentProps<"textarea">) {
  return (
    <textarea
      className={cn(inputBaseStyle, "min-h-[80px] py-3", className)}
      {...props}
    />
  );
}
