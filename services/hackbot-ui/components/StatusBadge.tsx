import type { RunStatus } from "@/lib/types";

const LABEL: Record<RunStatus, string> = {
  pending: "pending",
  running: "running",
  succeeded: "succeeded",
  failed: "failed",
  timed_out: "timed out",
};

export function StatusBadge({ status }: { status: RunStatus }) {
  return <span className={`badge ${status}`}>{LABEL[status] ?? status}</span>;
}
