declare module "claude-profiler" {
  export interface Subagent {
    id: string;
    meta: object;
    entries: unknown[];
  }
  export function createFirefoxProfile(
    entries: unknown[],
    subagents: Subagent[]
  ): object;
}
