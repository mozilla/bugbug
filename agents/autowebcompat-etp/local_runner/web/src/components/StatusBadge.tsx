import type { RunInfo, RunStatus } from "../api";

export function StatusBadge({ status }: { status: RunStatus | undefined }) {
  return <span className={`badge ${status ?? ""}`}>{status ?? "?"}</span>;
}

export function RunBadges({
  run,
}: {
  run: Pick<RunInfo, "status" | "applied">;
}) {
  return (
    <>
      <StatusBadge status={run.status} />
      {run.applied && <span className="badge applied">applied</span>}
    </>
  );
}
