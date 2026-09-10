# Trust boundaries

The central constraint: **an agent container is the least trusted component in the system.**
It runs model-directed code against untrusted input (bug reports, review comments, CI logs),
so it is given no durable credential and no ability to change anything directly.

## What the agent container can and cannot do

| Can                                                       | Cannot                                     |
| --------------------------------------------------------- | ------------------------------------------ |
| Read Bugzilla / Phabricator via the broker's loopback URL | Hold or read a Bugzilla or Phabricator key |
| Call Anthropic with a short-lived federated token         | Hold a long-lived Anthropic API key        |
| Write objects under its own run's GCS prefix              | Read or write another run's objects        |
| Edit its own ephemeral checkout                           | Push anywhere, or land anything            |
| _Record_ a Bugzilla comment or Phabricator revision       | Actually post one                          |

Each of those is a different mechanism.

## The broker sidecar

An agent that needs credentialed reads ships a second container from the same image. The
broker holds the third-party API keys (Secret Manager-backed, configured at deploy time) and
exposes them as capabilities over loopback (`BROKER_URL`, e.g. `http://127.0.0.1:8765`):

- `/{bugzilla,phabricator}/mcp` — read-only MCP tool servers, live during the run.
- `GET /phabricator/revision/{id}/patch` — a revision's base commit and raw diff, so a
  follow-up run can reproduce the revision's tree without a Conduit key.

It exposes only what a run legitimately needs, and only reads — every write goes through the
recorded-actions path instead. **Per-execution env overrides target the `agent` container by
name**, which is what stops a run's inputs from reaching or altering the broker's
environment.

Today `bug-fix`, `build-repair`, `frontend-triage` and `autowebcompat-repro` run a broker.
`test-repair` reaches an MCP server via an injected `BUGZILLA_MCP_URL` instead, and
`test-plan-generator` needs no credentialed reads at all. The invariant holds in every case:
**the key is never in the agent container.**

### Bugzilla reads are moving behind a proxy

A broker holding a Bugzilla key can read whatever that account can, for every run of that
agent. [bugzilla-proxy](bugzilla-proxy.md) replaces the key with a per-run capability token:
the credential moves to one shared service, and the broker holds only a signed statement of
what this run may read. The service and the minting exist; **no agent uses them yet**, and
every broker still holds its own key until the service is deployed and tested.

This is what makes private bugs reachable later, under a scope narrow enough to review. The
containment that has to land first is in [bugzilla-proxy.md](bugzilla-proxy.md).

## Workload Identity Federation

Anthropic and W&B both accept a Google-signed OIDC identity token exchanged for a
short-lived access token, so neither needs a static key in the container.

The runtime fetches that token from the GCP metadata server, writes it to a private file the
SDK reads, and keeps refreshing it in the background — Google tokens outlive neither a long
run nor a single exchange, so the file has to stay fresh for the whole run
([hackbot_runtime/anthropic_wif.py](../../libs/hackbot-runtime/hackbot_runtime/anthropic_wif.py)).

Federation is enabled by the presence of `ANTHROPIC_FEDERATION_RULE_ID`. An
`ANTHROPIC_API_KEY` set _alongside_ it is refused with an error, because the key would take
precedence and silently shadow federation.

## The signed upload policy

The agent Job has **no GCP write identity**. Its only write capability is a V4 signed GCS
POST policy minted per run by hackbot-api and passed in as env:

- `starts-with $key, runs/<run_id>/` — cannot write outside its own prefix.
- `content-length-range 0..5 GiB`.
- Expires at the job timeout plus a grace window.

Downloads for humans work the same way in reverse: hackbot-api mints a short-lived signed
GET URL for one artifact, and only for an artifact already listed on the run. The bucket is
never public.

On Cloud Run the API has no private key to sign with (the metadata server gives tokens
only), so it wraps its own credentials with `impersonated_credentials` targeting itself,
delegating `sign_bytes` to the IAM `signBlob` API. This is why its service account needs
`roles/iam.serviceAccountTokenCreator` **on itself**.

## Authenticating callers of hackbot-api

Four distinct schemes, one per class of caller:

| Caller                      | Scheme                                                                                 |
| --------------------------- | -------------------------------------------------------------------------------------- |
| UI, pulse listener, scripts | `X-API-Key`, compared in constant time                                                 |
| Phabricator                 | HMAC-SHA256 over the raw body, constant-time compared                                  |
| Slack                       | HMAC-SHA256 over `v0:{timestamp}:{raw body}`, plus a 5-minute timestamp window         |
| Eventarc / Pub/Sub push     | Google-signed OIDC bearer token, verified for audience **and** issuing service account |

The two HMAC schemes are not interchangeable: Slack's base string includes the delivery's
timestamp and that timestamp is checked against the clock, so a captured delivery cannot be
replayed. Phabricator's covers the body alone, which is why the Slack receiver has its own
verifier ([auth.py](../../services/hackbot-api/app/auth.py)). Neither key is optional, and
the Slack one must also be non-blank, so a deployment without a usable key fails to start
rather than quietly rejecting every delivery.

The push-token check is not redundant with platform IAM. The service allows unauthenticated
invocations — that is how API-key callers reach it at all — so IAM on the subscription does
not protect these routes on its own. The token is verified in the route.

## Authenticating humans (UI)

Google OAuth, `@mozilla.com` only, enforced twice: once in the OAuth callback before a
session is issued, and again on every server-side proxy request. Sessions are stateless
signed+encrypted cookies. The middleware's cookie check is an optimistic guard, not the
authority. See [triggers.md](triggers.md).

## Authorizing `@hackbot` mentions

A webhook signature proves the delivery came from Phabricator; it says nothing about _who_
commented. So the comment author must additionally be a member of the `bmo-editbugs-team`
project. [triggers.md](triggers.md) covers that check and the other guards on the path.

## Authorizing Slack clicks

The same split applies, and the second half is not built yet: a valid signature proves the
delivery came from the Slack app, not _who_ clicked, and a Slack user id is not an identity
this platform trusts. Resolving one to a `@mozilla.com` address (`users.info`, needing the
`users:read` and `users:read.email` scopes) and checking the workspace is what a click needs
before it can cause anything. Until then the receiver is inert by design, so no interactive
element exists ahead of the check that guards it.

## Recorded actions as a review gate

The record-then-apply split ([actions.md](actions.md)) is a security property as much as a
correctness one: proposed effects are inspectable before they land, auto-apply is off by
default, and only a `succeeded` run's actions are ever applied.

## Secrets

Secrets live in Secret Manager and are mounted as env vars on the service or Job that needs
them. Each deployed service runs as its **own least-privilege service account** granted
`secretmanager.secretAccessor` on only the secrets it reads. Locally, secrets come from a
root `.env`, which is never committed.
