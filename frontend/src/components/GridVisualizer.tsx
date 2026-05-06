import { cn } from "@/lib/utils";
import type { GridSlotData } from "@/types/api";

interface Props {
  slots: GridSlotData[]
  currentPrice: number | null
  className?: string
}

export default function GridVisualizer({ slots, currentPrice, className }: Props) {
  if (slots.length === 0) return null;

  // 计算可视范围: 从最低买入价到最高卖出价，上下各留 5% 余地
  const prices = slots.flatMap((s) => [s.buy_price, s.sell_price]);
  if (currentPrice) prices.push(currentPrice);
  const minP = Math.min(...prices);
  const maxP = Math.max(...prices);
  const range = maxP - minP || 0.001;
  const pad = range * 0.1;
  const bottom = minP - pad;
  const top = maxP + pad;
  const height = top - bottom;

  const yPos = (price: number) => ((top - price) / height * 100).toFixed(1);

  return (
    <div className={cn("relative w-full overflow-hidden rounded-[26px] border border-border surface-chart", className)}>
      {/* 价格标尺 */}
      <div className="absolute inset-y-0 left-0 flex flex-col justify-between px-3 py-4 text-[11px] font-mono text-muted-foreground select-none">
        {[0, 0.25, 0.5, 0.75, 1].map((t) => {
          const price = bottom + height * (1 - t);
          return (
            <span key={t} className="leading-none">
              ${price.toFixed(4)}
            </span>
          );
        })}
      </div>

      {/* 右侧图表区域 */}
      <div className="relative ml-20 mr-6 h-64">
        {/* 网格背景线 */}
        {[0, 0.25, 0.5, 0.75, 1].map((t) => (
          <div
            key={t}
            className="absolute left-0 right-0 border-t border-border/50"
            style={{ top: `${t * 100}%` }}
          />
        ))}

        {/* Slot 条 */}
        {slots.map((slot) => {
          const isBought = slot.status === "bought";
          const buyY = yPos(slot.buy_price);

          return (
            <div key={slot.slot_id} className="absolute left-0 right-0" style={{ top: `${buyY}%` }}>
              <div className="flex items-center gap-2 px-1">
                {/* 价格区间条 */}
                <div
                  className={cn(
                    "relative h-7 rounded-md border transition-colors flex-1",
                    isBought
                      ? "border-[color:var(--success)]/40 bg-[color:var(--success)]/10"
                      : "border-border bg-[color:var(--surface-soft)]/60",
                  )}
                  title={`slot ${slot.slot_id}: $${slot.buy_price} → $${slot.sell_price}`}
                >
                  {/* 买入价标记 */}
                  <div
                    className="absolute bottom-0 left-0 right-0 border-t border-dashed"
                    style={{
                      borderColor: isBought ? "var(--success)" : "var(--border)",
                      opacity: 0.5,
                    }}
                  />
                  {/* 标签：买入价（左） + slot号（中） + 卖出价（右） */}
                  <div className="absolute inset-0 flex items-center justify-between px-2 text-[10px] font-mono">
                    <span className="font-semibold" style={{ color: isBought ? "var(--success)" : "var(--muted-foreground)" }}>
                      B: ${slot.buy_price.toFixed(4)}
                    </span>
                    <span className={cn("text-[9px] font-semibold uppercase tracking-wider", isBought ? "text-[color:var(--success)]" : "text-muted-foreground")}>
                      #{slot.slot_id} {isBought ? "●" : "○"}
                    </span>
                    <span className="text-muted-foreground">
                      S: ${slot.sell_price.toFixed(4)}
                    </span>
                  </div>
                </div>

                {/* ROI 标签 */}
                {slot.roi_pct != null && (
                  <span
                    className="text-[11px] font-semibold font-mono whitespace-nowrap"
                    style={{ color: slot.roi_pct >= 0 ? "var(--success)" : "var(--danger)" }}
                  >
                    {slot.roi_pct >= 0 ? "+" : ""}{slot.roi_pct}%
                  </span>
                )}
              </div>
            </div>
          );
        })}

        {/* 当前价格线 */}
        {currentPrice != null && (
          <div
            className="absolute left-0 right-0 z-10 flex items-center gap-1 pointer-events-none"
            style={{ top: `${yPos(currentPrice)}%`, marginTop: "-7px" }}
          >
            <div className="size-3 rotate-45 border-2 border-[color:var(--primary)] bg-[color:var(--primary)]/20 rounded-[3px]" />
            <div className="h-px flex-1 bg-gradient-to-r from-[color:var(--primary)]/60 to-transparent" />
            <span className="text-[11px] font-bold font-mono text-[color:var(--primary)] bg-background/80 px-1.5 py-0.5 rounded-md">
              ${currentPrice.toFixed(4)}
            </span>
          </div>
        )}
      </div>
    </div>
  );
}
