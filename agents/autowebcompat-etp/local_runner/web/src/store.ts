// Application state, as one Valtio proxy. Components read it with
// `useSnapshot(store)`; only the functions in ./actions.ts mutate it.

import { proxy } from "valtio";

import type {
  AgentName,
  Applied,
  BugDraft,
  Config,
  Review,
  RunInfo,
  RunOptions,
  BugQuery,
  ListedBug,
  Resolution,
  WhoAmI,
} from "./api";

export const DEFAULT_OPTIONS: RunOptions = {
  model: null,
  effort: null,
  max_turns: null,
  headless: false,
  mode: "investigate",
  build: false,
};

/** The draft Bugzilla changes for the selected run, edited before posting. */
export interface ActionsState {
  bugId: string;
  comment: string;
  preview: boolean;
  changes: string;
  /** Loaded after the review, as it needs Bugzilla; null while it loads. */
  bugDraft: BugDraft | null;
  attachments: string[];
  addExceptions: boolean;
  close: boolean;
  resolution: Resolution;
  needinfo: boolean;
  force: boolean;
  busy: boolean;
  error: string | null;
  done: Applied | null;
  whoami: WhoAmI | null;
  whoamiError: string | null;
}

/** The selected run: its streamed output, then its result. */
export interface RunState {
  runId: string;
  lines: string[];
  info: RunInfo | null;
  ended: boolean;
  logOpen: boolean;
  cancelError: string | null;
  review: Review | null;
  reviewError: string | null;
  actions: ActionsState | null;
}

export type TriageStep = 1 | 2 | 3;

export interface TriageState {
  query: BugQuery;
  step: TriageStep;
  bugs: ListedBug[];
  selected: number[];
  options: RunOptions;
  busy: boolean;
  error: string | null;
}

export interface StartState {
  agent: AgentName;
  bugId: string;
  url: string;
  bugData: string;
  options: RunOptions;
  busy: boolean;
  error: string | null;
}

export const store = proxy({
  config: null as Config | null,
  configError: null as string | null,
  runs: [] as RunInfo[],
  start: {
    agent: "etp",
    bugId: "",
    url: "",
    bugData: "",
    options: { ...DEFAULT_OPTIONS },
    busy: false,
    error: null,
  } as StartState,
  run: null as RunState | null,
  /** The queue-from-Bugzilla dialog, while open. */
  triage: null as TriageState | null,
  /** Wall clock in ms, ticking while a run is in progress. */
  now: Date.now(),
});
