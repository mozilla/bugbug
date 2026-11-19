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
  const missingKeys = config && [
    ...(config.has_anthropic_key ? [] : ["ANTHROPIC_API_KEY"]),
    ...(config.has_bugzilla_key ? [] : ["BUGZILLA_API_KEY"]),
  ];

  return (
    <div className="layout">
      <aside>
        <h1>autowebcompat-etp</h1>
        {snap.configError && (
          <div className="note err">Server unreachable: {snap.configError}</div>
        )}
        {config && (
          <div className="meta">
            Artifacts: <span className="mono">{config.artifacts_root}</span>
            {!!missingKeys?.length && (
              <div className="note err">
                {missingKeys.join(", ")} missing (
                {config.env_file ?? ".env not found"})
              </div>
            )}
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
