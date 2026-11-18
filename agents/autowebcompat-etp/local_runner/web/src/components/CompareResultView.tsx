import { useSnapshot } from "valtio";

import {
  api,
  type CompareConfiguration,
  type CompareResult,
  type ScreenshotDiff,
} from "../api";
import { formatCost } from "../format";
import { store } from "../store";

const CONFIGURATIONS: { name: CompareConfiguration; label: string }[] = [
  { name: "off", label: "No tracker blocking" },
  { name: "strict", label: "Strict ETP" },
  { name: "easyprivacy", label: "Strict with EasyPrivacy" },
  { name: "easylist", label: "Strict with EasyList + EasyPrivacy" },
];

const OUTCOME_BADGES: Record<CompareResult["outcome"], string> = {
  sites_same: "ok",
  broken: "error",
  site_unavailable: "cancelled",
};

function describeDiff(diff: Readonly<ScreenshotDiff> | undefined): string {
  if (!diff) return "—";
  if (diff.identical) return "identical";
  if (diff.changed_fraction === null) {
    return `different size (${diff.size.join(
      "×"
    )} vs ${diff.reference_size.join("×")})`;
  }
  return `${(diff.changed_fraction * 100).toFixed(1)}% of pixels differ`;
}

/** The etp_compare agent's result: the outcome and each configuration's loads. */
export function CompareResultView() {
  const { run } = useSnapshot(store);
  const review = run?.review;
  const result = review?.compare_result;
  if (!run || !review || !result) return null;
  const { runId } = run;
  const { screenshots, screenshot_labels } = review;
  const findings = review.summary.findings;
  const broken = new Set(result.broken_configurations);

  return (
    <>
      <h2>Result</h2>
      <div className="card">
        <p>
          <a href={result.url} target="_blank" rel="noreferrer">
            {result.url}
          </a>
        </p>
        <p>{result.summary}</p>
        <dl className="kv">
          <dt>Outcome</dt>
          <dd>
            <span className={`badge ${OUTCOME_BADGES[result.outcome]}`}>
              {result.outcome}
            </span>
            {!result.llm_used && (
              <span className="meta"> (identical screenshots, no LLM)</span>
            )}
          </dd>
          <dt>Turns / cost</dt>
          <dd>
            {findings.num_turns ?? "—"} /{" "}
            {formatCost(findings.total_cost_usd, 4)}
          </dd>
        </dl>
        <p>
          {review.has_agent_log && (
            <>
              <a href={api.fileUrl(runId, "logs/agent.log")} target="_blank">
                Full agent log
              </a>
              {" · "}
            </>
          )}
          {result.content_blocking_log_url && (
            <a
              href={api.fileUrl(runId, result.content_blocking_log_url)}
              target="_blank"
            >
              Content blocking log
            </a>
          )}
        </p>
      </div>

      <h3>Configurations</h3>
      <table className="configs">
        <thead>
          <tr>
            <th>Configuration</th>
            <th>Blocked trackers</th>
            <th>Screenshot vs no blocking</th>
            <th>Verdict</th>
          </tr>
        </thead>
        <tbody>
          {CONFIGURATIONS.map(({ name, label }) => {
            const origins = result.blocked_tracker_origins[name] ?? [];
            return (
              <tr key={name}>
                <td>{label}</td>
                <td>
                  {origins.length ? (
                    <details>
                      <summary>{result.blocked_trackers[name] ?? "—"}</summary>
                      {origins.map((origin) => (
                        <div key={origin} className="mono">
                          {origin}
                        </div>
                      ))}
                    </details>
                  ) : (
                    result.blocked_trackers[name] ?? "—"
                  )}
                </td>
                <td>
                  {name === "off"
                    ? "reference"
                    : describeDiff(result.screenshot_diffs[name])}
                </td>
                <td>
                  {name === "off" ? (
                    "—"
                  ) : broken.has(name) ? (
                    <span className="tags">
                      <span className="badge error">broken</span>
                      {(result.breakage_tags?.[name] ?? []).map((tag) => (
                        <span key={tag} className="badge error">
                          {tag}
                        </span>
                      ))}
                    </span>
                  ) : result.outcome === "site_unavailable" ? (
                    "—"
                  ) : (
                    <span className="badge ok">works</span>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>

      {Object.keys(screenshots).length > 0 && (
        <>
          <h3>Screenshots</h3>
          <div className="shots">
            {Object.entries(screenshots).map(([name, key]) => (
              <figure key={name}>
                <a href={api.fileUrl(runId, key)} target="_blank">
                  <img src={api.fileUrl(runId, key)} alt={name} />
                </a>
                <figcaption>{screenshot_labels[name] ?? name}</figcaption>
              </figure>
            ))}
          </div>
        </>
      )}
    </>
  );
}
