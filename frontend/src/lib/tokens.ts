/**
 * Known Base chain token address → name/display mapping.
 * Add entries as new tokens are encountered.
 */
const TOKEN_NAMES: Record<string, string> = {
  "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913": "USDC",
  "0xfde4c96c8593536e31f229ea8f37b2ada2699bb2": "USDT",
  "0x0b3e328455c4059eeb9e3f84b5543f74e24e7e1b": "VIRTUAL",
}

/** Shorten an address for display: 0x1234...5678 */
export function shortenAddress(addr: string, prefix = 6, suffix = 4): string {
  if (addr.length <= prefix + suffix + 3) return addr
  return `${addr.slice(0, prefix)}...${addr.slice(-suffix)}`
}

/** Look up a token's display name, or return the shortened address. */
export function tokenDisplayName(addr: string): string {
  const key = addr.toLowerCase()
  return TOKEN_NAMES[key] ?? shortenAddress(addr, 6, 4)
}

/** Format a UTC ISO timestamp to UTC+8 time string (HH:mm:ss). */
export function formatTime(isoStr: string | null | undefined): string {
  if (!isoStr) return "-";
  const d = new Date(isoStr);
  if (isNaN(d.getTime())) return "-";
  return d.toLocaleString("zh-CN", {
    timeZone: "Asia/Shanghai",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  });
}

/** Format a token amount to a readable string (auto decimal places). */
export function formatTokenAmount(amount: number): string {
  if (amount === 0) return "0"
  if (amount >= 1000) return amount.toFixed(2)
  if (amount >= 1) return amount.toFixed(4)
  if (amount >= 0.001) return amount.toFixed(6)
  return amount.toFixed(8)
}
