// Everything that changes the store: API calls, the run's event stream, and
// form edits that need more than a plain field assignment.

import {
  api,
  type BugDraft,
  type BugQuery,
  type Review,
  type RunInfo,
} from "./api";
import {
  DEFAULT_OPTIONS,
  store,
  type RunState,
  type TriageStep,
} from "./store";

const RUNS_POLL_MS = 5000;

const message = (err: unknown) => (err as Error).message;

// --- app --------------------------------------------------------------------

// The selected run lives in the URL hash (#<run_id>) so reloads keep it.
const runFromHash = () => decodeURIComponent(location.hash.slice(1)) || null;

/** Load config and runs, then keep the run list and the hash in sync. */
export function init(): () => void {
  api.config().then(
    (config) => (store.config = config),
    (err) => (store.configError = message(err))
  );
  loadRuns();
  const poll = setInterval(loadRuns, RUNS_POLL_MS);
  const tick = setInterval(() => {
    if (store.run?.info?.status === "running") store.now = Date.now();
  }, 1000);
  const onHash = () => openRun(runFromHash());
  addEventListener("hashchange", onHash);
  onHash();
  return () => {
    clearInterval(poll);
    clearInterval(tick);
    removeEventListener("hashchange", onHash);
    closeStream();
  };
}

export function loadRuns() {
  api.runs().then(
    (runs) => (store.runs = runs),
    () => {}
  );
}

export function selectRun(runId: string) {
  location.hash = encodeURIComponent(runId);
}

export function listedRun(runId: string): RunInfo | undefined {
  return store.runs.find((r) => r.run_id === runId);
}

// --- starting runs ----------------------------------------------------------

export async function startRun() {
  const start = store.start;
  start.busy = true;
  start.error = null;
  try {
    const run = await api.startRun(
      start.agent === "etp_compare"
        ? {
            ...start.options,
            mode: "investigate",
            agent: "etp_compare",
            url: start.url.trim(),
          }
        : {
            ...start.options,
            agent: "etp",
            bug_id: start.bugId ? Number(start.bugId) : null,
            bug_data: start.bugData || null,
          }
    );
    loadRuns();
    selectRun(run.run_id);
  } catch (err) {
    start.error = message(err);
  } finally {
    start.busy = false;
  }
}

// --- the selected run -------------------------------------------------------

let source: EventSource | null = null;

function closeStream() {
  source?.close();
  source = null;
}

/** Select a run and follow its output; the server replays it from the start. */
function openRun(runId: string | null) {
  if (store.run?.runId === runId) return;
  closeStream();
  if (runId === null) {
    store.run = null;
    return;
  }
  store.run = {
    runId,
    lines: [],
    info: null,
    ended: false,
    logOpen: true,
    cancelError: null,
    review: null,
    reviewError: null,
    actions: null,
  };
  const run = store.run;
  const stream = (source = new EventSource(api.streamUrl(runId)));

  // Each message is a batch of lines; a replayed log arrives as one.
  stream.onmessage = (e) => {
    run.lines.push(...(JSON.parse(e.data) as string[]));
  };
  stream.addEventListener("status", (e) => {
    run.info = JSON.parse((e as MessageEvent).data) as RunInfo;
    store.now = Date.now();
  });
  stream.addEventListener("end", () => {
    stream.close();
    source = null;
    run.ended = true;
    run.logOpen = false;
    loadRuns();
    loadReview(run);
  });
}

export async function cancelRun() {
  const run = store.run!;
  run.cancelError = null;
  try {
    await api.cancel(run.runId);
  } catch (err) {
    run.cancelError = message(err);
  }
}

async function loadReview(run: RunState) {
  try {
    const review = await api.review(run.runId);
    run.review = review;
    run.reviewError = null;
    // Keep the user's edits when reloading after an apply.
    run.actions ??= {
      bugId: review.draft.bug_id ? String(review.draft.bug_id) : "",
      comment: review.draft.comment,
      preview: false,
      changes: "",
      bugDraft: review.result ? null : { changes: {}, reporter: null },
      attachments: [...review.draft.attachments],
      addExceptions: false,
      close: review.draft.close,
      needinfo: defaultNeedinfo(review, false),
      resolution: review.draft.resolution ?? "INVALID",
      force: false,
      busy: false,
      error: null,
      done: null,
      whoami: null,
      whoamiError: null,
    };
    const actions = run.actions;
    if (!actions.bugDraft) {
      api.bugDraft(run.runId).then(
        (bugDraft) => {
          actions.bugDraft = bugDraft;
          // Unless the reviewer has started typing their own.
          if (!actions.changes) {
            actions.changes = JSON.stringify(
              exceptionTagsOptional(review.result)
                ? withoutExceptionTags(bugDraft.changes)
                : bugDraft.changes
            );
          }
        },
        (err) => {
          actions.bugDraft = { changes: {}, reporter: null };
          actions.error = `Could not load the bug's fields: ${message(err)}`;
        }
      );
    }
    if (!actions.whoami && review.result) {
      api.whoami().then(
        (who) => (actions.whoami = who),
        (err) => (actions.whoamiError = message(err))
      );
    }
  } catch (err) {
    run.reviewError = message(err);
  }
}

// --- Bugzilla actions -------------------------------------------------------

/**
 * The reporter to needinfo, if known. The reporter is the drafted bug's, so
 * not once the bug ID has been changed to another bug.
 */
export function needinfoReporter(
  actions: { bugId: string; bugDraft: BugDraft | null },
  review: { draft: { bug_id: number | null } }
): string | null {
  if (actions.bugId !== String(review.draft.bug_id)) return null;
  return actions.bugDraft?.reporter ?? null;
}

export function toggleAttachment(stage: string, on: boolean) {
  const { attachments } = store.run!.actions!;
  const i = attachments.indexOf(stage);
  if (on && i < 0) attachments.push(stage);
  if (!on && i >= 0) attachments.splice(i, 1);
}

// The fields that carry the tags the Remote Settings bot adds exceptions from.
const EXCEPTION_TAG_FIELDS = ["whiteboard", "cf_user_story"];

/**
 * Whether the reviewer has to opt in to the exception tags: exceptions that
 * only get around an anti-adblock wall aren't added by default.
 */
export function exceptionTagsOptional(
  result: {
    failure_reason: string | null;
    exception_records: readonly unknown[];
  } | null
): boolean {
  return (
    result?.failure_reason === "anti_adblock" &&
    result.exception_records.length > 0
  );
}

function withoutExceptionTags(changes: Record<string, unknown>) {
  return Object.fromEntries(
    Object.entries(changes).filter(
      ([field]) => !EXCEPTION_TAG_FIELDS.includes(field)
    )
  );
}

/**
 * Whether "needinfo the reporter" starts ticked: not when the exception tags
 * are added, since deploying the exceptions needinfos the reporter.
 */
function defaultNeedinfo(review: Review, addExceptions: boolean): boolean {
  const tagsAdded =
    !!review.result?.exception_records.length &&
    (!exceptionTagsOptional(review.result) || addExceptions);
  return review.draft.needinfo && !tagsAdded;
}

/** Add the draft's exception tag fields to the field changes, or remove them. */
export function setAddExceptions(on: boolean) {
  const actions = store.run!.actions!;
  const draft = actions.bugDraft?.changes ?? {};
  let changes: Record<string, unknown>;
  try {
    changes = JSON.parse(actions.changes || "{}");
  } catch {
    actions.error = "Field changes is not valid JSON";
    return;
  }
  changes = withoutExceptionTags(changes);
  if (on) {
    for (const field of EXCEPTION_TAG_FIELDS) {
      if (field in draft) changes[field] = draft[field];
    }
  }
  actions.error = null;
  actions.addExceptions = on;
  actions.needinfo = defaultNeedinfo(store.run!.review!, on);
  actions.changes = JSON.stringify(changes);
}

export async function applyActions() {
  const run = store.run!;
  const actions = run.actions!;
  let changes: Record<string, unknown>;
  try {
    changes = JSON.parse(actions.changes || "{}");
  } catch {
    actions.error = "Field changes is not valid JSON";
    return;
  }
  const bugId = Number(actions.bugId);
  if (!bugId) {
    actions.error = "Bug ID is required";
    return;
  }
  const fields = Object.keys(changes);
  const reporter = needinfoReporter(actions, run.review!);
  const needinfo = actions.needinfo && reporter ? reporter : null;
  const parts = [
    actions.comment.trim() && "a comment",
    fields.length && `field changes (${fields.join(", ")})`,
    actions.attachments.length &&
      `${actions.attachments.length} screenshot(s) combined into one image`,
    actions.close && `close as RESOLVED ${actions.resolution}`,
    needinfo && `a needinfo for ${needinfo}`,
  ].filter(Boolean);
  if (!parts.length) {
    actions.error = "Nothing to post";
    return;
  }
  if (!confirm(`Post ${parts.join(", ")} to bug ${bugId}?`)) return;

  actions.busy = true;
  actions.error = null;
  try {
    actions.done = await api.apply(run.runId, {
      bug_id: bugId,
      comment: actions.comment,
      changes,
      attachments: [...actions.attachments],
      resolution: actions.close ? actions.resolution : null,
      needinfo,
      force: actions.force,
    });
    actions.force = false;
    loadRuns();
    loadReview(run);
  } catch (err) {
    actions.error = message(err);
  } finally {
    actions.busy = false;
  }
}

// --- queue from a Bugzilla list: fetch -> select -> queue -------------------

export function openTriage(query: BugQuery) {
  store.triage = {
    query,
    step: 1,
    bugs: [],
    selected: [],
    options: { ...DEFAULT_OPTIONS },
    busy: false,
    error: null,
  };
  fetchBugs();
}

export function closeTriage() {
  store.triage = null;
}

export function triageGoTo(step: TriageStep) {
  store.triage!.error = null;
  store.triage!.step = step;
}

export async function fetchBugs() {
  const triage = store.triage!;
  triage.busy = true;
  triage.error = null;
  try {
    const bugs = await api.bugs(triage.query);
    triage.bugs = bugs;
    triage.selected = bugs.filter((b) => !b.runs.length).map((b) => b.id);
    triage.step = 2;
  } catch (err) {
    triage.error = message(err);
  } finally {
    triage.busy = false;
  }
}

export function selectBugs(pred: (hasRuns: boolean) => boolean) {
  const triage = store.triage!;
  triage.selected = triage.bugs
    .filter((b) => pred(b.runs.length > 0))
    .map((b) => b.id);
}

export function toggleBug(id: number, on: boolean) {
  const triage = store.triage!;
  const selected = new Set(triage.selected);
  if (on) selected.add(id);
  else selected.delete(id);
  // Keep the table's order, which is also the queue order.
  triage.selected = triage.bugs
    .filter((b) => selected.has(b.id))
    .map((b) => b.id);
}

export async function queueSelected() {
  const triage = store.triage!;
  triage.busy = true;
  triage.error = null;
  try {
    const runs = await api.startBatch({
      ...triage.options,
      bug_ids: [...triage.selected],
    });
    closeTriage();
    loadRuns();
    if (runs.length) selectRun(runs[0].run_id);
  } catch (err) {
    triage.error = message(err);
    triage.busy = false;
  }
}
