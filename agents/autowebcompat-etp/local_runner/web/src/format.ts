import type { RunInfo } from "./api";

export const EFFORTS = ["low", "medium", "high", "xhigh", "max"] as const;

export function formatTime(seconds: number | null | undefined): string {
  return seconds ? new Date(seconds * 1000).toLocaleString() : "";
}

export function formatDuration(run: RunInfo, now: number): string {
  if (!run.started) return "";
  const total = Math.round((run.finished ?? now / 1000) - run.started);
  return `${Math.floor(total / 60)}m${String(total % 60).padStart(2, "0")}s`;
}

export function formatCost(
  cost: number | null | undefined,
  digits = 2
): string {
  return cost == null ? "—" : `$${cost.toFixed(digits)}`;
}

export function brokenLabel(value: boolean | null): string {
  if (value === true) return "broken";
  if (value === false) return "works";
  return "not tested";
}

export function isActive(run: RunInfo): boolean {
  return run.active && run.finished == null;
}
