import { useSnapshot } from "valtio";

import { selectRun } from "../actions";
import { formatCost, formatTime } from "../format";
import { store } from "../store";
import { RunBadges } from "./StatusBadge";

export function RunList() {
  const { runs, run: selected } = useSnapshot(store);
  if (!runs.length) return <p className="meta">No runs yet.</p>;
  return (
    <ul className="runs">
      {runs.map((run) => (
        <li
          key={run.run_id}
          className={run.run_id === selected?.runId ? "sel" : ""}
          onClick={() => selectRun(run.run_id)}
        >
          <div className="id">{run.run_id}</div>
          <div className="meta">
            <RunBadges run={run} /> {formatTime(run.started ?? run.queued)}
            {run.turns ? ` · ${run.turns} turns` : ""}
            {run.cost ? ` · ${formatCost(run.cost)}` : ""}
          </div>
        </li>
      ))}
    </ul>
  );
}
