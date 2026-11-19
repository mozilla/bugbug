import { useEffect, useRef, type MouseEvent, type ReactNode } from "react";
import { useSnapshot } from "valtio";

import {
  closeTriage,
  fetchBugs,
  queueSelected,
  selectBugs,
  toggleBug,
  triageGoTo,
} from "../actions";
import type { BugQuery } from "../api";
import { store, type TriageStep } from "../store";
import { RunOptionsFields } from "./RunOptionsFields";
import { StatusBadge } from "./StatusBadge";

const STEPS: [TriageStep, string][] = [
  [1, "Fetch"],
  [2, "Select"],
  [3, "Queue"],
];

const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? "" : "s"}`;

const COMPONENT = <b>Web Compatibility :: Privacy: Site Reports</b>;

const QUERIES: Record<
  BugQuery,
  {
    noun: string;
    description: ReactNode;
    dateLabel: string;
    dateField: "creation_time" | "last_change_time";
  }
> = {
  untriaged: {
    noun: "untriaged bug",
    description: (
      <>
        open, untriaged {COMPONENT} defects (severity <code>--</code>)
      </>
    ),
    dateLabel: "Filed",
    dateField: "creation_time",
  },
  open: {
    noun: "open bug",
    description: (
      <>
        all open {COMPONENT} bugs, most recently changed first, without{" "}
        <code>[meta]</code> bugs
      </>
    ),
    dateLabel: "Changed",
    dateField: "last_change_time",
  },
};

/** Fetch ETP site reports from Bugzilla, pick some, queue a run for each. */
export function TriageDialog() {
  const dialog = useRef<HTMLDialogElement>(null);
  const { config, triage } = useSnapshot(store);

  useEffect(() => {
    dialog.current?.showModal();
  }, []);

  if (!triage || !config) return null;
  const { step, bugs, busy, error } = triage;
  const query = QUERIES[triage.query];
  const selected = new Set(triage.selected);
  const pick = (pred: (hasRuns: boolean) => boolean) => (e: MouseEvent) => {
    e.preventDefault();
    selectBugs(pred);
  };

  let body;
  let nextButton;
  if (step === 1) {
    body = (
      <>
        <p>Fetch {query.description} from Bugzilla.</p>
        <p className="meta">
          <a href={config.query_urls[triage.query]} target="_blank">
            Open the query in Bugzilla
          </a>
        </p>
        {busy && <p>Fetching…</p>}
      </>
    );
    nextButton = (
      <button onClick={fetchBugs} disabled={busy}>
        {busy ? "Fetching…" : "Fetch bugs"}
      </button>
    );
  } else if (step === 2) {
    body = bugs.length ? (
      <>
        <p className="meta">
          {plural(bugs.length, query.noun)}. Bugs that already have a run start
          unselected.{" "}
          <a href="#" onClick={pick(() => true)}>
            Select all
          </a>{" "}
          ·{" "}
          <a href="#" onClick={pick(() => false)}>
            None
          </a>{" "}
          ·{" "}
          <a href="#" onClick={pick((hasRuns) => !hasRuns)}>
            Only without runs
          </a>
        </p>
        <table className="bugs">
          <thead>
            <tr>
              <th />
              <th>Bug</th>
              <th>Summary</th>
              <th>{query.dateLabel}</th>
              <th>Runs</th>
            </tr>
          </thead>
          <tbody>
            {bugs.map((bug) => (
              <tr key={bug.id} className={bug.runs.length ? "done" : ""}>
                <td>
                  <input
                    type="checkbox"
                    checked={selected.has(bug.id)}
                    onChange={(e) => toggleBug(bug.id, e.target.checked)}
                  />
                </td>
                <td>
                  <a
                    href={`${config.bugzilla}/show_bug.cgi?id=${bug.id}`}
                    target="_blank"
                  >
                    {bug.id}
                  </a>
                </td>
                <td>
                  {bug.summary}
                  {bug.url && <div className="meta mono">{bug.url}</div>}
                </td>
                <td className="meta">{bug[query.dateField].slice(0, 10)}</td>
                <td>
                  {bug.runs.map((run) => (
                    <span key={run.run_id} title={run.run_id}>
                      <StatusBadge status={run.status} />{" "}
                    </span>
                  ))}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </>
    ) : (
      <p>No {query.noun}s. 🎉</p>
    );
    nextButton = (
      <button onClick={() => triageGoTo(3)} disabled={!selected.size}>
        Next ({selected.size} selected)
      </button>
    );
  } else {
    const ids = triage.selected;
    body = (
      <>
        <p>
          Queue <b>{plural(ids.length, "run")}</b>:{" "}
          {ids.map((id, i) => (
            <span key={id}>
              {i > 0 && ", "}
              <code>{id}</code>
            </span>
          ))}
          .
        </p>
        <p className="meta">
          Runs execute{" "}
          {config.max_parallel === 1
            ? "one at a time"
            : `${config.max_parallel} at a time`}{" "}
          (set <code>MAX_PARALLEL</code> to change). Each run has its own cost.
        </p>
        <RunOptionsFields
          options={store.triage!.options}
          buildLabel="Rebuild image before the first run"
          compact
        />
      </>
    );
    nextButton = (
      <button onClick={queueSelected} disabled={busy}>
        Queue {plural(ids.length, "run")}
      </button>
    );
  }

  return (
    <dialog ref={dialog} onClose={closeTriage}>
      <div className="dlg-head">
        <div className="steps">
          {STEPS.map(([n, label]) => (
            <span key={n} className={n === step ? "on" : ""}>
              {n}. {label}
            </span>
          ))}
        </div>
        <button
          className="closex"
          title="Close"
          onClick={() => dialog.current?.close()}
        >
          ×
        </button>
      </div>
      <div className="dlg-body">
        {body}
        {error && <div className="note err">{error}</div>}
      </div>
      <div className="dlg-foot">
        <button
          className="secondary"
          style={{ visibility: step > 1 ? "visible" : "hidden" }}
          onClick={() => triageGoTo((step - 1) as TriageStep)}
        >
          Back
        </button>
        {nextButton}
      </div>
    </dialog>
  );
}
