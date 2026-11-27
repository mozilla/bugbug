import { useSnapshot } from "valtio";

import type { Effort, RunOptions } from "../api";
import { EFFORTS } from "../format";
import { store } from "../store";

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
  const { config, models } = useSnapshot(store);
  const modelName = models?.models.find((m) => m.id === snap.model);

  const limit = (
    label: string,
    key: "max_turns" | "timeout_minutes",
    fallback: number | null | undefined
  ) => (
    <div>
      <label>{label}</label>
      <input
        type="number"
        min={1}
        placeholder={fallback ? String(fallback) : "no limit"}
        value={snap[key] ?? ""}
        onChange={(e) =>
          (options[key] = e.target.value ? Number(e.target.value) : null)
        }
      />
    </div>
  );
  const maxTurns = limit("Max turns", "max_turns", config?.default_max_turns);
  const timeout = limit(
    "Timeout (min)",
    "timeout_minutes",
    config?.run_timeout_minutes
  );

  return (
    <>
      <div className="row">
        <div>
          <label>Model</label>
          <select
            value={snap.model ?? ""}
            onChange={(e) => (options.model = e.target.value || null)}
          >
            <option value="">default</option>
            {snap.model && !modelName && (
              <option value={snap.model}>{snap.model}</option>
            )}
            {models?.models.map((m) => (
              <option key={m.id} value={m.id}>
                {m.display_name} ({m.id})
              </option>
            ))}
          </select>
          {models?.fallback && (
            <div className="meta" title={models.error}>
              Couldn't list models; showing a fixed list.
            </div>
          )}
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
        {compact && timeout}
      </div>
      {!compact && (
        <div className="row">
          {maxTurns}
          {timeout}
        </div>
      )}
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
