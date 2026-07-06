// Tray-meter helpers (glance pivot). The fill is quantized to 5% steps before crossing IPC so
// per-poll noise never re-renders the menu-bar icon; the Rust side applies its own visual floor.
export function quantizeFill(share: number | null): number | null {
  if (share == null || !Number.isFinite(share)) return null;
  const clamped = Math.max(0, Math.min(1, share));
  return Math.round(clamped * 20) / 20;
}
