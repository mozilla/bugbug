# Triggers: how runs start

Every path ends at the same call — `POST /agents/{agent}/runs`. Nothing bypasses the API to
start an execution directly, which is what keeps run state complete regardless of origin.

```
hackbot-ui ─────────────┐
pulse-listener ─────────┤
Phabricator webhook ────┼──> POST /agents/{agent}/runs ──> Cloud Run Job execution
Bugzilla webhook ───────┘
```

## hackbot-ui — humans

A Next.js app on Cloud Run. It can trigger any agent in its list, filter and page through
recent runs, poll a run to terminal state, render findings, download artifacts via
short-lived signed URLs, review recorded actions and apply them, and retrigger a failed run
with the same inputs.

**The API key never reaches the browser.** Every call goes through a server-side route
handler under `app/api/*` that re-validates the session and injects `X-API-Key`
([lib/hackbot.ts](../../services/hackbot-ui/lib/hackbot.ts)). Retrigger reads the original inputs server-side, so the browser only
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

> **Being migrated to Treeherder.** This section documents the current implementation; it
> should be removed from these docs once the migration lands.

An always-on Cloud Run **worker pool** (no HTTP port) that consumes `task-failed` messages
from `pulse.mozilla.org`, decides which failures are worth an agent, and dispatches
`build-repair` (failed build tasks) or `test-repair` (failed test tasks). Dispatch is where
its involvement ends: the agent reports its own result, as an `email.send` action (and, for
test-repair, a Slack message) applied once the run has succeeded.

**It holds no investigation logic.** Each agent resolves the push, the commit range and the
failing tests itself from the task id. The listener only decides _what to hand off_ — which
keeps the expensive reasoning in one place and lets the filter stay cheap.

The filtering is the substance of this service and changes often, so it is documented
where it lives: [services/hackbot-pulse-listener/README.md](../../services/hackbot-pulse-listener/README.md).
In outline, a failure has to survive routing by project and task kind, a discard pass for
failures this push did not introduce, per-push dedupe, a newness check (Treeherder
classification then an ancestor walk), and a daily run budget before an agent is dispatched.

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

## Bugzilla webhook — `needinfo?` requests

A `needinfo?` request directed at Hackbot's Bugzilla account triggers a `bug-fix` follow-up
run on that bug. Same shape as the Phabricator trigger — a developer asks Hackbot for
something in the place they already work — but on the Bugzilla side, and answering a
question rather than revising a patch.

The delivery is authenticated by a **shared secret** BMO sends verbatim in
`X-Bugzilla-Webhook-Secret`, compared in constant time
([auth.py](../../services/hackbot-api/app/auth.py)). Unlike Phabricator's payload, BMO's
carries the full bug and the change set, so the receiver needs no callback to detect a
qualifying request — see
[bugzilla_webhook.py](../../services/hackbot-api/app/bugzilla_webhook.py).

A request qualifies only when the modification is `modify` on a `bug`, the bug is public,
and the change set contains `flag.needinfo` added as `? (<bot login>)` with a matching
`needinfo?` flag in the bug's current flags. The **routing key is deliberately not
checked**, because one update may change several fields at once.

Guards, each closing a specific failure mode:

- **Loop prevention** — the bot's own flag changes are ignored, so answering a needinfo
  cannot retrigger a run.
- **Private bugs are never processed** — the check is `is_private is not False`, so a
  missing or non-boolean value fails closed.
- **Dedupe** — retried deliveries are deduped by the **needinfo flag id**, which is globally
  unique, so a later needinfo on the same bug gets a new id and still triggers. As on the
  Phabricator side, the key is claimed **only after a successful trigger**, keeping a
  transient failure retryable by BMO.
- **Latest flag wins** — BMO orders flags by id, so the last matching one is the newly
  requested one.

Only requesters in Bugzilla's `editbugs` group are authorized (all Mozilla Corporation
members belong to this group) — see
[bugzilla_authorization.py](../../services/hackbot-api/app/bugzilla_authorization.py).
Membership is checked per login with BMO's server-side `group_ids` filter on `/rest/user`.

- An unauthorized request is ignored without
  consuming the dedupe key, leaving the same flag eligible for a later delivery after the
  requester becomes authorized.

The receiver passes the requester's login and the change timestamp to the agent as context
for locating the accompanying comment — a needinfo may be filed without one, in which case
the agent falls back to the surrounding bug context. That text is **passed through as data**;
the prompt tells the agent to treat bug content as data, not as instructions.

Unlike the Phabricator follow-up, this run prepares an ordinary source checkout — there is
no revision to apply — so it can create a **new** Phabricator revision but cannot update an
existing one. The needinfo flag is cleared automatically as a recorded
`bugzilla.update_bug` action once the run produces at least one other action, coalesced with
the reply comment into a single Bugzilla transaction (see [actions.md](actions.md)). A run
that records nothing leaves the flag standing.

Configuration is four env vars — `BUGZILLA_WEBHOOK_SECRET` (required, no default),
`BUGZILLA_WEBHOOK_BOT_LOGIN`, `BUGZILLA_WEBHOOK_URL` and
`BUGZILLA_WEBHOOK_DEDUPE_TTL_SECONDS`; see [deployment.md](deployment.md).
