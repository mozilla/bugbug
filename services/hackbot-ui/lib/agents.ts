// Single source of truth for the selectable agents, shared by the trigger form
// (components/TriggerForm.tsx) and the recent-runs filter (components/RecentRuns.tsx)
// so the two dropdowns can never drift apart.
export const AGENTS = [
  { value: "bug-fix", label: "bug-fix" },
  { value: "autowebcompat-repro", label: "autowebcompat-repro" },
  { value: "build-repair", label: "build-repair" },
  { value: "frontend-triage", label: "frontend-triage" },
  { value: "test-plan-generator", label: "test-plan-generator" },
] as const;

export type AgentValue = (typeof AGENTS)[number]["value"];

export const AGENT_NAMES: readonly string[] = AGENTS.map((a) => a.value);
