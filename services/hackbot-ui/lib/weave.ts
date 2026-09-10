const DEFAULT_WEAVE_PROJECT = "moz-bugbug/hackbot-dev";

// Weave project ("entity/project") the agents trace into; server-side only.
export function weaveProject(): string {
  return process.env.WEAVE_PROJECT || DEFAULT_WEAVE_PROJECT;
}

// Weave Agents view filtered to the conversations tagged with this run id
// (hackbot-runtime stamps it on every span as attributes.hackbot.run_id).
export function weaveRunTracesUrl(project: string, runId: string): string {
  return (
    `https://wandb.ai/${project}/weave/agents/conversations` +
    `?filters[custom_attrs_string:wandb.attributes.hackbot.run_id]=${encodeURIComponent(runId)}`
  );
}
