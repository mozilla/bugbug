// Mirror of the hackbot-api response models (services/hackbot-api/app/schemas.py).

export type RunStatus =
  | "pending"
  | "running"
  | "succeeded"
  | "failed"
  | "timed_out";

export const TERMINAL_STATUSES: RunStatus[] = [
  "succeeded",
  "failed",
  "timed_out",
];

export function isTerminal(status: RunStatus): boolean {
  return TERMINAL_STATUSES.includes(status);
}

export interface AgentDescriptor {
  name: string;
  description: string;
  // JSON Schema describing the agent's accepted inputs.
  input_schema: Record<string, unknown>;
}

export interface ArtifactRef {
  name: string;
  size: number;
  content_type: string | null;
}

export interface RunSummary {
  status: string;
  error: string | null;
  findings: Record<string, unknown>;
}

export type RunActionStatus = "pending" | "applied" | "failed";

// Mirror of RunActionDoc (services/hackbot-api/app/schemas.py): a recorded
// agent action and its apply state.
export interface RunAction {
  idx: number;
  type: string;
  params: Record<string, unknown>;
  ref: string | null;
  status: RunActionStatus;
  result: Record<string, unknown> | null;
  error: string | null;
  applied_at: string | null;
}

export interface RunRef {
  run_id: string;
  agent: string;
  status: RunStatus;
}

export type FeedbackRating = "up" | "down";

// Keep in step with FeedbackDimension (services/hackbot-api/app/schemas.py);
// the API rejects labels it doesn't know.
export const FEEDBACK_DIMENSIONS = [
  { value: "root_cause_wrong", label: "Root cause is wrong" },
  { value: "fix_wont_work", label: "Proposed fix won't work" },
  { value: "wrong_files", label: "Wrong files or broken links" },
  { value: "overconfident", label: "Overconfident for what it knew" },
  {
    value: "should_not_have_commented",
    label: "Shouldn't have commented at all",
  },
  { value: "too_verbose", label: "Too verbose" },
] as const;

export type FeedbackDimension = (typeof FEEDBACK_DIMENSIONS)[number]["value"];

// Mirror of FeedbackTargetDoc (services/hackbot-api/app/schemas.py).
export interface FeedbackTarget {
  bug_id: number;
  comment: string;
  nonce: string;
}

export interface RunDoc {
  run_id: string;
  agent: string;
  status: RunStatus;
  inputs: Record<string, unknown>;
  requested_by: string | null;
  created_at: string;
  updated_at: string;
  execution_name: string | null;
  results_prefix: string;
  summary: RunSummary | null;
  artifacts: ArtifactRef[];
  error: string | null;
}
