// The description of a pending fix: the title and summary the recorded
// `phabricator.submit_patch` action would give its revision. This is what the
// patch "says", so the patch panel shows it above the diff.
export interface RevisionMessage {
  title: string;
  summary: string | null;
}

export function revisionMessage(
  actions: { type: string; params: Record<string, unknown> }[] | null
): RevisionMessage | null {
  const action = actions?.find((a) => a.type === "phabricator.submit_patch");
  if (!action) return null;
  const text = (v: unknown) => (typeof v === "string" && v.trim() ? v : null);
  const title = text(action.params.title);
  return title ? { title, summary: text(action.params.summary) } : null;
}
