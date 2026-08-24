# Actions: record now, apply later

An agent never mutates Bugzilla, Phabricator, TestRail or Slack, and never sends mail,
while it runs. It calls a
tool that **records what it intends to do**; hackbot-api performs it after the run has
finished and is known good.

Why the indirection:

- A run that fails halfway leaves **no half-applied side effects**.
- Every intent is **reviewable** — visible in `summary.json` and in the UI, with the
  agent's own stated reasoning, before anything lands.
- Applying is **idempotent and retryable**, which matters because the event delivery that
  drives it is at-least-once.
- Auto-apply is **per-agent opt-in**, so a new or unproven agent can run in
  propose-only mode with no code change.

## Recording (inside the run)

The write-action tools are declared in [hackbot_runtime/actions/](../../libs/hackbot-runtime/hackbot_runtime/actions/) — one module per domain,
using the same `@tool` decorator as [read tools](tools.md). Calling one records the intent
against the run's `ActionsRecorder` instead of performing it.

The recorded list becomes `summary.json`'s `actions` array. Any attached files are
published as run artifacts and referenced by key, since the local path disappears with the
container.

### The catalog

Each action type has a declaration the agent calls and a handler that applies it.
[actions/handlers/registry.py](../../libs/hackbot-runtime/hackbot_runtime/actions/handlers/registry.py) is the authoritative type → handler map.

As with read tools, nothing is exposed by default: an agent lists the dotted types it may
record in its `config.py` and passes them to `actions_server_for`, which builds a server
carrying only those. `bug-fix`, for instance, allows `phabricator.submit_patch` on a fresh
triage run but swaps it for `phabricator.update_patch` on a follow-up.

| Action type                 | Records the intent to…                     | Params                                    |
| --------------------------- | ------------------------------------------ | ----------------------------------------- |
| `bugzilla.update_bug`       | Change a bug's fields                      | `bug_id`, `changes`                       |
| `bugzilla.add_comment`      | Comment on a bug                           | `bug_id`, `text`, `is_private`            |
| `bugzilla.add_attachment`   | Attach a file to a bug                     | `bug_id`, + a `file` attachment           |
| `bugzilla.create_bug`       | File a new bug                             | the new bug's fields                      |
| `phabricator.submit_patch`  | Deliver a fix as a **new** revision        | `bug_id`, `title`, `summary`, `test_plan` |
| `phabricator.update_patch`  | Add a new diff to an **existing** revision | `revision_id`                             |
| `phabricator.add_comment`   | Reply on a revision without changing code  | `revision_id`, `text`                     |
| `testrail.submit_test_plan` | Submit a generated test plan to TestRail   | the validated feature + test cases        |
| `slack.post_message`        | Post a message to Slack                    | `channel`, `text`                         |
| `email.send`                | Email a report about the run               | `to`, `subject`, `body_markdown`          |

All but `testrail.submit_test_plan` and `email.send` take a **`reasoning`** argument — a free-text audit trail
stored on the action and shown in the UI beside the proposed change. `phabricator.submit_patch`
is the only model-facing tool that exposes **`ref`** (see cross-references below).

`testrail` and `slack` also provide `record_test_plan` / `record_message` helpers that agent
code calls directly rather than the model choosing to — for an action the agent always takes
once it has a result, not one the model decides on. `email.send` is _only_ that: it has no
model-facing tool, since who receives mail is the agent code's decision. Its recipient
policy is apply-side — `NOTIFICATION_TEAM_EMAIL` is copied on every email and used as
`Reply-To`, and `NOTIFICATION_OVERRIDE_EMAIL` redirects everything to one address so a
development deployment cannot mail real developers. Sending needs `SENDGRID_API_KEY` and
`NOTIFICATION_SENDER` on hackbot-api.

`bugzilla.add_comment` appends a feedback-reaction footer to every recorded comment, and
`is_private=true` marks it security-group-only.

Adding a type is a declaration in the domain module plus one line in the handler registry.
The dispatch loop never changes.

### The two patch actions

`submit_patch` and `update_patch` are deliberately separate, each taking only the
parameters its own case needs, so a model cannot create a revision when it meant to update
one by getting an optional argument wrong. That is the general pattern for write-actions:
prefer several narrow tools over one with mode flags.

Neither takes a patch file. The agent's final working-tree state _is_ the diff — which is
why the runtime builds the Phabricator payload during `publish_changes`, while the checkout
still exists.

### Hooks

`ActionsRecorder` supports per-type hooks that run before an action is appended. A hook may
mutate the action (enrichment) or raise (validation gate — nothing is recorded and no
attachment is published). They live on the recorder rather than in the tool declarations so
the runtime can attach cross-cutting behaviour without every handler knowing about it.

The Searchfox permalink expansion on `bugzilla.add_comment` is the working example: the
agent writes `{{searchfox.permalink}}/path/to/file.js#412`, the hook expands it at record
time, and the comment awaiting review already shows clickable URLs.

## Applying (after the run)

Triggered by the `run.completed` event, on a subscription filtered to **succeeded** runs.

1. **Record rows.** Every entry in `summary.json`'s `actions` is upserted as a
   `run_actions` row (`pending`), keyed `(run_id, idx)`. This happens for _all_ succeeded
   runs, whether or not the agent auto-applies, so the UI can always show and apply them.
2. **Apply, if opted in.** With `auto_apply_actions=True` on the agent's registry entry,
   pending rows are applied immediately. Otherwise they wait for a human to click apply.
3. **Dispatch.** Each row's `type` selects a handler from the registry. The handler gets
   the params and an `ApplyContext` — which can `download_artifact(key)` without knowing
   GCS is behind it, keeping the runtime library free of a storage dependency.
4. **Stamp.** The row records `applied` or `failed`, its result, and its error. Only a real
   success sets `applied_at`.

Rows are committed one action at a time, and an `applied` row is never reapplied — so a
retried event or a repeated manual apply-all is safe and resumes where it stopped.

Actions from a run that is not `succeeded` are never applied. The subscription filters
them out and the applier checks again, because acting on a run that never reached a
verified-good state is not wanted even if it recorded something before erroring.

## Cross-action references

An action's result often isn't known until it's applied — a Phabricator revision has no URL
until it exists. So a later action can reference an earlier one's result by label:

```
submit_patch(..., ref="patch")
add_comment(text="Patch up for review: {{actions.patch.url}}")
```

`{{actions.<ref>.<field>}}` is substituted at apply time, recursively through params.
Resolution draws on rows already `applied` in earlier passes as well as this one, so a
later manual apply can still reference an earlier action's result.

An unresolvable placeholder is **left as-is and logged**, rather than raising. The action
then fails with an error a human can read, instead of silently posting mangled text.

## Bugzilla coalescing

Same-bug field changes are merged with the closest comment into a single
`PUT /bug/{id}`, so Bugzilla applies them as one transaction — one bugmail, one history
entry, instead of a burst. Other comments on that bug still apply separately. A group is
applied at its last member's index, once every earlier dependency has resolved, and any
group whose rows carry a `ref` is excluded (nothing should reference a coalesced member's
result).

## Where to look

| Concern                          | File                                                                                                                       |
| -------------------------------- | -------------------------------------------------------------------------------------------------------------------------- |
| Recording mechanics, hooks       | [libs/hackbot-runtime/hackbot_runtime/actions/recorder.py](../../libs/hackbot-runtime/hackbot_runtime/actions/recorder.py) |
| Action declarations (per domain) | [hackbot_runtime/actions/](../../libs/hackbot-runtime/hackbot_runtime/actions/)                                            |
| Apply-side handlers              | [libs/hackbot-runtime/hackbot_runtime/actions/handlers/](../../libs/hackbot-runtime/hackbot_runtime/actions/handlers/)     |
| Type → handler map               | [actions/handlers/registry.py](../../libs/hackbot-runtime/hackbot_runtime/actions/handlers/registry.py)                    |
| Orchestration, refs, coalescing  | [services/hackbot-api/app/actions_applier.py](../../services/hackbot-api/app/actions_applier.py)                           |

Record side and apply side deliberately live in the **same library**, so the set of
actions an agent can request and the set the platform can apply cannot drift apart.
