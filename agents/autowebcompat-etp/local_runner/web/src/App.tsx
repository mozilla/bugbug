import { useEffect } from "react";
import { useSnapshot } from "valtio";

import { init, openTriage } from "./actions";
import { RunList } from "./components/RunList";
import { RunView } from "./components/RunView";
import { StartForm } from "./components/StartForm";
import { TriageDialog } from "./components/TriageDialog";
import { store } from "./store";

export function App() {
  const snap = useSnapshot(store);
  useEffect(init, []);

  const config = snap.config;

  return (
    <div className="layout">
      <aside>
        <h1>autowebcompat-etp</h1>
        {snap.configError && (
          <div className="note err">Server unreachable: {snap.configError}</div>
        )}
        {config && (
          <div className="meta">
            <div>Artifacts:</div>
            <div className="mono path">{config.artifacts_root}</div>
            {config.setup_problems.map((problem) => (
              <div key={problem} className="note err">
                {problem}
              </div>
            ))}
          </div>
        )}
        <StartForm />
        <button
          type="button"
          className="secondary wide"
          disabled={!config}
          onClick={() => openTriage("untriaged")}
        >
          Queue untriaged bugs…
        </button>
        <button
          type="button"
          className="secondary wide"
          disabled={!config}
          onClick={() => openTriage("open")}
        >
          Queue open bugs…
        </button>
        <h2>Runs</h2>
        <RunList />
      </aside>
      <main>
        {snap.run && config ? (
          <RunView />
        ) : (
          <div className="empty">Start a run or pick one on the left.</div>
        )}
      </main>
      {snap.triage && config && <TriageDialog />}
    </div>
  );
}
