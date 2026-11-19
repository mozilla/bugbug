// Types and client for the FastAPI endpoints in ../server.py.

export type RunStatus =
  | "queued"
  | "running"
  | "ok"
  | "error"
  | "cancelled"
  | "interrupted";

export type Effort = "low" | "medium" | "high" | "xhigh" | "max";

/**
 * `etp` investigates a bug report; `etp_compare` compares a site with and
 * without tracker blocking.
 */
export type AgentName = "etp" | "etp_compare";

/** `retest` reruns an old bug with Firefox's ETP exceptions turned off. */
export type Mode = "investigate" | "retest";

export interface RunOptions {
  model: string | null;
  effort: Effort | null;
  max_turns: number | null;
  /** Minutes before the runner stops the run; null: the server's default. */
  timeout_minutes: number | null;
  headless: boolean;
  mode: Mode;
  build: boolean;
}

export interface RunParams extends Partial<RunOptions> {
  agent?: AgentName;
  bug_id?: number | null;
  bug_data?: string | null;
  url?: string | null;
}

export interface RunInfo {
  run_id: string;
  params: RunParams;
  status: RunStatus;
  queued?: number | null;
  started: number | null;
  finished: number | null;
  turns: number | null;
  cost: number | null;
  active: boolean;
  applied?: boolean;
}

/** A model the Models API lists, or one of the server's fallbacks. */
export interface ModelInfo {
  id: string;
  display_name: string;
}

export interface ModelList {
  models: ModelInfo[];
  /** The Models API couldn't be reached; these are a fixed list. */
  fallback: boolean;
  error?: string;
}

export interface Config {
  artifacts_root: string;
  bugzilla: string;
  env_file: string | null;
  has_anthropic_key: boolean;
  has_bugzilla_key: boolean;
  max_parallel: number;
  /** Limits for a run that sets none; null: no limit. */
  default_max_turns: number | null;
  run_timeout_minutes: number | null;
  /** Setup steps that look skipped or broken; empty when setup is complete. */
  setup_problems: string[];
  query_urls: Record<BugQuery, string>;
}

/** The Bugzilla bug lists runs can be queued from. */
export type BugQuery = "untriaged" | "open";

export interface ProposedException {
  category: "baseline" | "convenience";
  urlPattern: string;
  topLevelUrlPattern: string | null;
  classifierFeatures: string[];
  rationale: string;
}

export interface ProposedShim {
  kind: "opt_in_placeholder" | "stub";
  name: string;
  matches: string[];
  unblocksOnOptIn: string[];
  behavior: string;
  existing_shim_id: string | null;
  rationale: string;
}

export interface EtpResult {
  summary: string;
  failure_reason: string | null;
  broken_in_standard: boolean | null;
  broken_in_strict: boolean | null;
  broken_with_easyprivacy?: boolean | null;
  broken_with_easylist?: boolean | null;
  /** The Firefox the run drove (absent in older runs). */
  firefox_version?: string | null;
  firefox_build_id?: string | null;
  culprit_prefs: string[];
  blocked_resources: string[];
  proposed_exceptions: ProposedException[];
  proposed_shim: ProposedShim | null;
  exception_verified: boolean;
  severity: "S1" | "S2" | "S3" | "S4" | null;
  priority: "P1" | "P2" | "P3" | null;
  steps: string;
  exception_records: Record<string, unknown>[];
  whiteboard_tags: string[];
  user_story_lines: string[];
  existing_exceptions: Record<string, unknown>[] | null;
  existing_shims: Record<string, unknown>[] | null;
  screenshot_urls: Record<string, string>;
}

export type CompareConfiguration =
  | "off"
  | "strict"
  | "easyprivacy"
  | "easylist";
export type BlockingConfiguration = Exclude<CompareConfiguration, "off">;

export interface ScreenshotDiff {
  identical: boolean;
  size: readonly [number, number];
  reference_size: readonly [number, number];
  bbox: readonly [number, number, number, number] | null;
  changed_fraction: number | null;
}

/** The etp_compare agent's result. */
export interface CompareResult {
  url: string;
  outcome: "sites_same" | "broken" | "site_unavailable";
  broken_configurations: BlockingConfiguration[];
  breakage_tags?: Partial<Record<BlockingConfiguration, string[]>>;
  summary: string;
  blocked_trackers: Partial<Record<CompareConfiguration, number>>;
  blocked_tracker_origins: Partial<Record<CompareConfiguration, string[]>>;
  screenshot_diffs: Partial<Record<BlockingConfiguration, ScreenshotDiff>>;
  llm_used: boolean;
  screenshot_urls: Record<string, string>;
  content_blocking_log_url: string | null;
}

export interface Summary {
  status: "ok" | "error";
  error: string | null;
  findings: {
    result?: EtpResult | CompareResult;
    num_turns?: number;
    total_cost_usd?: number;
    traceback?: string;
  };
}

export interface AppliedAction {
  action: string;
  stage?: string;
  stages?: string[];
  fields?: string[];
  ids?: number[];
  with_comment?: boolean;
}

export interface Applied {
  applied_at: string;
  bug_id: number;
  url: string;
  actions: AppliedAction[];
  error: string | null;
}

export interface Draft {
  bug_id: number | null;
  comment: string;
  attachments: string[];
  resolution: Resolution | null;
  /** Whether "close the bug" starts ticked. */
  close: boolean;
  /**
   * Whether the outcome calls for needinfo'ing the reporter. Not when the
   * exception tags are added: deploying the exceptions needinfos them later.
   */
  needinfo: boolean;
}

/** The resolutions the bug can be closed with (DUPLICATE needs a dup_id). */
export const RESOLUTIONS = [
  "FIXED",
  "INVALID",
  "WONTFIX",
  "INACTIVE",
  "WORKSFORME",
  "INCOMPLETE",
] as const;

export type Resolution = (typeof RESOLUTIONS)[number];

/** The parts of the draft that need the bug's current fields from Bugzilla. */
export interface BugDraft {
  changes: Record<string, unknown>;
  /** The reporter, who can be needinfo'd to confirm a fix. */
  reporter: string | null;
}

export interface Review {
  agent: AgentName;
  summary: Summary;
  /** Only for the etp agent. */
  result: EtpResult | null;
  /** Only for the etp_compare agent. */
  compare_result: CompareResult | null;
  screenshots: Record<string, string>;
  screenshot_labels: Record<string, string>;
  has_agent_log: boolean;
  applied: Applied | null;
  draft: Draft;
}

export interface ListedBug {
  id: number;
  summary: string;
  creation_time: string;
  last_change_time: string;
  url: string;
  status: string;
  whiteboard: string;
  runs: { run_id: string; status: RunStatus }[];
}

export interface ApplyRequest {
  bug_id: number;
  comment: string;
  changes: Record<string, unknown>;
  attachments: string[];
  resolution: Resolution | null;
  needinfo: string | null;
  force: boolean;
}

export interface WhoAmI {
  name: string;
  real_name?: string;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  const body = await res.json().catch(() => null);
  if (!res.ok) {
    const detail = body?.detail ?? body;
    throw new Error(
      typeof detail === "string" ? detail : JSON.stringify(detail)
    );
  }
  return body as T;
}

const post = <T>(path: string, body?: unknown) =>
  request<T>(path, {
    method: "POST",
    body: body === undefined ? undefined : JSON.stringify(body),
  });

const runPath = (runId: string) => `/api/runs/${encodeURIComponent(runId)}`;

export const api = {
  config: () => request<Config>("/api/config"),
  models: () => request<ModelList>("/api/models"),
  whoami: () => request<WhoAmI>("/api/whoami"),
  runs: () => request<RunInfo[]>("/api/runs"),
  startRun: (params: RunParams) => post<RunInfo>("/api/runs", params),
  startBatch: (body: RunOptions & { bug_ids: number[] }) =>
    post<RunInfo[]>("/api/runs/batch", body),
  cancel: (runId: string) => post<RunInfo>(`${runPath(runId)}/cancel`),
  review: (runId: string) => request<Review>(`${runPath(runId)}/review`),
  bugDraft: (runId: string) => request<BugDraft>(`${runPath(runId)}/bug-draft`),
  apply: (runId: string, body: ApplyRequest) =>
    post<Applied>(`${runPath(runId)}/apply`, body),
  bugs: (query: BugQuery) => request<ListedBug[]>(`/api/bugs/${query}`),
  streamUrl: (runId: string) => `${runPath(runId)}/stream`,
  fileUrl: (runId: string, key: string) => `${runPath(runId)}/files/${key}`,
};
