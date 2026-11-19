import { useSnapshot } from "valtio";

import type { Effort, RunOptions } from "../api";
import { EFFORTS } from "../format";

interface Props {
  /** The store's options proxy to edit in place. */
  options: RunOptions;
  buildLabel?: string;
  compact?: boolean;
  /** The retest checkbox, which only the etp agent understands. */
  showMode?: boolean;
}

/** Model / effort / max turns / headless / mode / build, shared by both start flows. */
export function RunOptionsFields({
  options,
  buildLabel = "Rebuild image first",
  compact = false,
  showMode = true,
}: Props) {
  const snap = useSnapshot(options, { sync: true });

  const maxTurns = (
    <div>
      <label>Max turns</label>
      <input
        type="number"
        placeholder="default"
        value={snap.max_turns ?? ""}
        onChange={(e) =>
          (options.max_turns = e.target.value ? Number(e.target.value) : null)
        }
      />
    </div>
  );

  return (
    <>
      <div className="row">
        <div>
          <label>Model</label>
          <input
            placeholder="default"
            value={snap.model ?? ""}
            onChange={(e) => (options.model = e.target.value || null)}
          />
        </div>
        <div>
          <label>Effort</label>
          <select
            value={snap.effort ?? ""}
            onChange={(e) =>
              (options.effort = (e.target.value || null) as Effort | null)
            }
          >
            <option value="">default</option>
            {EFFORTS.map((effort) => (
              <option key={effort}>{effort}</option>
            ))}
          </select>
        </div>
        {compact && maxTurns}
      </div>
      {!compact && maxTurns}
      <label className="check">
        <input
          type="checkbox"
          checked={snap.headless}
          onChange={(e) => (options.headless = e.target.checked)}
        />
        Headless Firefox
      </label>
      {showMode && (
        <label className="check">
          <input
            type="checkbox"
            checked={snap.mode === "retest"}
            onChange={(e) =>
              (options.mode = e.target.checked ? "retest" : "investigate")
            }
          />
          Retest with ETP exceptions disabled
        </label>
      )}
      <label className="check">
        <input
          type="checkbox"
          checked={snap.build}
          onChange={(e) => (options.build = e.target.checked)}
        />
        {buildLabel}
      </label>
    </>
  );
}
