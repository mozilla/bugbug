// The agents the launchpad can trigger. Shared between the trigger form and the
// recent-runs panel so both agree on the selected agent (carried in the
// `?agent=` URL search param).

export const AGENTS = [
  { value: "bug-fix", label: "bug-fix" },
  { value: "autowebcompat-repro", label: "autowebcompat-repro" },
  { value: "build-repair", label: "build-repair" },
  { value: "frontend-triage", label: "frontend-triage" },
  { value: "test-plan-generator", label: "test-plan-generator" },
] as const;

export type AgentValue = (typeof AGENTS)[number]["value"];

export const DEFAULT_AGENT: AgentValue = "bug-fix";

export function parseAgent(value: string | null): AgentValue {
  return AGENTS.some((a) => a.value === value)
    ? (value as AgentValue)
    : DEFAULT_AGENT;
}
