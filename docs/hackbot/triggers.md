# Triggers: how runs start

Every path ends at the same call — `POST /agents/{agent}/runs`. Nothing bypasses the API to
start an execution directly, which is what keeps run state complete regardless of origin.

```
hackbot-ui ─────────────┐
pulse-listener ─────────┼──> POST /agents/{agent}/runs ──> Cloud Run Job execution
Phabricator webhook ────┘
```

## hackbot-ui — humans

A Next.js app on Cloud Run. It can trigger any agent in its list, filter and page through
recent runs, poll a run to terminal state, render findings, download artifacts via
short-lived signed URLs, review recorded actions and apply them, and retrigger a failed run
with the same inputs.

**The API key never reaches the browser.** Every call goes through a server-side route
handler under `app/api/*` that re-validates the session and injects `X-API-Key`
(`lib/hackbot.ts`). Retrigger reads the original inputs server-side, so the browser only
sends a run id.

**Auth is Google OAuth via better-auth, `@mozilla.com` only, and completely stateless** —
no database. The session lives in a signed + encrypted (JWE) cookie; the only shared state
is `BETTER_AUTH_SECRET`. That makes it Cloud Run-friendly out of the box: sessions survive
scale-to-zero and work across any number of instances, as long as every instance shares the
secret.

The domain restriction is enforced in two independent layers: the Google provider's
`mapProfileToUser` rejects a non-Mozilla identity during the OAuth callback, before a
session is issued, and `getAuthedEmail()` re-checks the domain on every proxy request.
`middleware.ts` is only an optimistic cookie-presence guard — it redirects to `/login`, or
returns 401 JSON for `/api/*`, but is not the authority.

Runs are attributed to the signed-in user via `X-On-Behalf-Of`, including retriggers
(attributed to whoever clicked, not the original requester).

Adding an agent to the UI means adding it to `lib/agents.ts`, the shared list behind both
the trigger form and the run filter.

## hackbot-pulse-listener — Taskcluster CI

An always-on Cloud Run **worker pool** (no HTTP port) that consumes `task-failed` messages
from `pulse.mozilla.org`, decides which failures are worth an agent, and dispatches
`build-repair` (failed build tasks) or `test-repair` (failed test tasks). When the run
finishes it polls the result and emails a report.

**It holds no investigation logic.** Each agent resolves the push, the commit range and the
failing tests itself from the task id. The listener only decides _what to hand off_ — which
keeps the expensive reasoning in one place and lets the filter stay cheap.

The filtering is the substance of this service, and
[`services/hackbot-pulse-listener/README.md`](../../services/hackbot-pulse-listener/README.md)
documents it properly. The shape:

1. **Route** by watched project and task kind.
2. **Discard** what isn't this push's failure: action-task-scheduled tasks (backfills,
   retriggers) and pushes older than `MAX_PUSH_AGE_HOURS`.
3. **Dedupe** per push per agent, in memory.
4. **Judge** whether the failure is new. Test failures go through a Treeherder
   classification gate first (the cheap filter — most stop here), then an ancestor walk
   comparing failing manifests within the same configuration; build failures compare task
   labels.
5. **Budget** — at most `MAX_TEST_REPAIRS_PER_DAY` test-repair runs per rolling 24h, since
   each one clones and builds Firefox.
6. **Dispatch and report** — trigger, poll to terminal, email.

Three properties worth carrying in your head when changing it:

- **Every check fails open.** An upstream error runs the agent rather than dropping a
  possible regression.
- **Dedupe keys are claimed only when a run is actually triggered**, so a task rejected as
  intermittent or inherited leaves the push open for the next one.
- **All state is in-memory** — dedupe caches, the daily budget, pending-run tracking. A
  restart resets them.

## Phabricator webhook — `@hackbot` mentions

An `@hackbot` mention in a comment on a Differential revision triggers a `bug-fix` follow-up
run against that revision.

The delivery is authenticated by **Phabricator's HMAC-SHA256 signature** over the raw body,
not the API key. The payload carries only PHIDs, so the receiver calls Conduit to fetch the
triggering transactions, find the mention, and resolve the revision to a revision id plus
Bugzilla bug id. A revision with no bug id is skipped — `bug-fix` needs one.

Guards, each closing a specific failure mode:

- **Loop prevention** — comments authored by the bot's own PHID are ignored.
- **Authorization** — the comment author must belong to the `bmo-editbugs-team` Phabricator
  project. Membership is cached with a short TTL; an unknown author triggers one refresh so
  new members take effect promptly, then a cooldown so unauthorized deliveries don't cause a
  Conduit call each.
- **Dedupe** — retried deliveries are deduped by triggering transaction PHID, and a
  transaction is marked seen **only after a successful trigger**. A transient Conduit
  failure therefore 500s and gets reprocessed on retry rather than dropped as a duplicate.
- **Fresh transactions only** — a payload mixing new and already-seen PHIDs can't
  re-trigger on an old one.

One review can leave several inline comments, each its own transaction; all qualifying ones
are combined and passed to the agent as XML-tagged `<comment>` elements carrying the comment
id, type and diff id. The comment text is **passed through as data** — the agent's prompts
frame identity and scope, not the receiver.

The receiver triggers over the **public API** (a loopback call while co-located) rather than
calling the database and job internals directly, so splitting it into its own service later
is a matter of repointing a URL.

The follow-up run then uses `checkout_revision` to prepare its tree at the revision's base
commit with the revision's diff applied — see [runtime.md](runtime.md).
