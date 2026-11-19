import { useSnapshot } from "valtio";

import { api } from "../api";
import { brokenLabel, formatCost } from "../format";
import { store } from "../store";

function Json({ title, value }: { title: string; value: unknown }) {
  return (
    <>
      <h3>{title}</h3>
      <pre className="log">{JSON.stringify(value, null, 2)}</pre>
    </>
  );
}

export function ResultView() {
  const { run } = useSnapshot(store);
  if (!run?.review) return null;
  const { runId, review } = run;
  const { summary, result, screenshots, screenshot_labels } = review;
  const findings = summary.findings;
  const logLink = review.has_agent_log && (
    <p>
      <a href={api.fileUrl(runId, "logs/agent.log")} target="_blank">
        Full agent log
      </a>
    </p>
  );

  if (summary.status !== "ok" || !result) {
    return (
      <>
        <h2>Result</h2>
        <div className="note err">
          <b>Run failed:</b> {summary.error}
        </div>
        {findings.traceback && (
          <details>
            <summary>Traceback</summary>
            <pre className="log">{findings.traceback}</pre>
          </details>
        )}
        {logLink}
      </>
    );
  }

  return (
    <>
      <h2>Result</h2>
      <div className="card">
        <p>{result.summary}</p>
        <dl className="kv">
          <dt>Outcome</dt>
          <dd>
            {result.failure_reason ? (
              <code>{result.failure_reason}</code>
            ) : (
              <>
                <b className="ok">fix proposed</b> (verified:{" "}
                {String(result.exception_verified)})
              </>
            )}
          </dd>
          <dt>Standard / Strict</dt>
          <dd>
            {brokenLabel(result.broken_in_standard)} /{" "}
            {brokenLabel(result.broken_in_strict)}
          </dd>
          <dt>Strict with EasyPrivacy</dt>
          <dd>{brokenLabel(result.broken_with_easyprivacy ?? null)}</dd>
          <dt>Strict with EasyList + EasyPrivacy</dt>
          <dd>{brokenLabel(result.broken_with_easylist ?? null)}</dd>
          <dt>Severity / priority</dt>
          <dd>
            {result.severity ?? "—"} / {result.priority ?? "—"}
          </dd>
          <dt>Culprit prefs</dt>
          <dd>
            {result.culprit_prefs.length
              ? result.culprit_prefs.map((pref, i) => (
                  <span key={pref}>
                    {i > 0 && ", "}
                    <code>{pref}</code>
                  </span>
                ))
              : "—"}
          </dd>
          <dt>Blocked</dt>
          <dd>
            {result.blocked_resources.length
              ? result.blocked_resources.map((resource) => (
                  <div key={resource}>{resource}</div>
                ))
              : "—"}
          </dd>
          <dt>Turns / cost</dt>
          <dd>
            {findings.num_turns ?? "—"} /{" "}
            {formatCost(findings.total_cost_usd, 4)}
          </dd>
        </dl>
        {logLink}
      </div>

      {result.exception_records.length > 0 && (
        <Json
          title="Proposed exception records"
          value={result.exception_records}
        />
      )}
      {result.proposed_shim && (
        <Json title="Proposed shim" value={result.proposed_shim} />
      )}
      {result.existing_exceptions === null && (
        <div className="note">Could not fetch the existing exception list.</div>
      )}
      {!!result.existing_exceptions?.length && (
        <Json
          title="Overlapping existing exceptions"
          value={result.existing_exceptions}
        />
      )}
      {!!result.existing_shims?.length && (
        <Json
          title="Overlapping existing shims"
          value={result.existing_shims}
        />
      )}

      {Object.keys(screenshots).length > 0 && (
        <>
          <h3>Screenshots</h3>
          <div className="shots">
            {Object.entries(screenshots).map(([stage, key]) => (
              <figure key={stage}>
                <a href={api.fileUrl(runId, key)} target="_blank">
                  <img src={api.fileUrl(runId, key)} alt={stage} />
                </a>
                <figcaption>{screenshot_labels[stage] ?? stage}</figcaption>
              </figure>
            ))}
          </div>
        </>
      )}

      <details>
        <summary>Steps</summary>
        <pre className="steps-text">{result.steps}</pre>
      </details>
    </>
  );
}
