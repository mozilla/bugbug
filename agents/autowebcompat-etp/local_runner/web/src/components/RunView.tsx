import { useSnapshot } from "valtio";

import { store } from "../store";
import { ActionsForm } from "./ActionsForm";
import { CompareResultView } from "./CompareResultView";
import { CopyButton } from "./CopyButton";
import { LogView } from "./LogView";
import { Progress } from "./Progress";
import { ResultView } from "./ResultView";

/** The selected run's progress, output and review. */
export function RunView() {
  const { run, runs, config } = useSnapshot(store);
  if (!run) return null;
  const listed = runs.find((r) => r.run_id === run.runId);
  // The listed info includes `applied`, so prefer it once the run is over.
  const info = run.ended ? listed ?? run.info : run.info ?? listed;

  return (
    <>
      <h1 className="mono">{run.runId}</h1>
      {!!info?.params.bug_id && config && (
        <p className="meta">
          Bugzilla link:{" "}
          <a
            href={`${config.bugzilla}/show_bug.cgi?id=${info.params.bug_id}`}
            target="_blank"
            rel="noreferrer"
          >
            {`${config.bugzilla}/show_bug.cgi?id=${info.params.bug_id}`}
          </a>{" "}
          <CopyButton
            text={`${config.bugzilla}/show_bug.cgi?id=${info.params.bug_id}`}
            label="copy link"
          />{" "}
          <CopyButton text={String(info.params.bug_id)} label="copy bug ID" />
        </p>
      )}
      {info ? (
        <Progress info={info} />
      ) : (
        <div className="card meta">Connecting…</div>
      )}
      <details
        open={run.logOpen}
        onToggle={(e) => {
          if (store.run) store.run.logOpen = e.currentTarget.open;
        }}
      >
        <summary>Output ({run.lines.length} lines)</summary>
        <LogView lines={run.lines} />
      </details>
      {run.reviewError && (
        <div className="note err">No result: {run.reviewError}</div>
      )}
      {run.review?.compare_result && run.review.summary.status === "ok" ? (
        <CompareResultView />
      ) : (
        run.review && (
          <>
            <ResultView />
            {run.review.result &&
              run.review.summary.status === "ok" &&
              run.actions && <ActionsForm />}
          </>
        )
      )}
    </>
  );
}
