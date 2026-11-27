import { useSnapshot } from "valtio";

import { cancelRun } from "../actions";
import type { RunInfo } from "../api";
import { formatCost, formatDuration, isActive } from "../format";
import { store } from "../store";
import { RunBadges } from "./StatusBadge";

export function Progress({ info }: { info: RunInfo }) {
  const { now, run } = useSnapshot(store);
  const active = isActive(info);
  const queued = info.status === "queued";

  const subject = info.params.bug_id
    ? `bug ${info.params.bug_id}`
    : info.params.bug_data
      ? "inline report"
      : "—";

  return (
    <div className="card">
      <dl className="kv">
        <dt>Status</dt>
        <dd>
          <RunBadges run={info} />
        </dd>
        <dt>Subject</dt>
        <dd>{subject}</dd>
        <dt>Elapsed</dt>
        <dd>{queued ? "waiting in queue" : formatDuration(info, now)}</dd>
        <dt>Turns</dt>
        <dd>{info.turns ?? "—"}</dd>
        <dt>Cost</dt>
        <dd>{formatCost(info.cost, 4)}</dd>
      </dl>
      {active && (
        <p>
          <button className="danger" onClick={cancelRun}>
            {queued ? "Remove from queue" : "Cancel run"}
          </button>
        </p>
      )}
      {run?.cancelError && <div className="note err">{run.cancelError}</div>}
    </div>
  );
}
