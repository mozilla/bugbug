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

export interface RunDoc {
  run_id: string;
  agent: string;
  status: RunStatus;
  inputs: Record<string, unknown>;
  created_at: string;
  updated_at: string;
  execution_name: string | null;
  results_prefix: string;
  summary: RunSummary | null;
  artifacts: ArtifactRef[];
  error: string | null;
}
