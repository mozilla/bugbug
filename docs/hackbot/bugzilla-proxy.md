# Bugzilla proxy

> **Status: the service is built, nothing uses it yet.** Phases 1 to 4 are proposed.
> [services/bugzilla-proxy/](../../services/bugzilla-proxy/) exists and is tested, and
> hackbot-api can mint tokens. **No agent is wired to it**: no `AgentSpec` sets a
> `bugzilla_scope`, so no token is minted and every broker still uses its own Bugzilla
> credential. Onboarding the first agent comes after the service is deployed and tested.
> No private bug is reachable through any of this: that is phase 3, and it depends on the
> containment work in phase 2. See [Delivery phases](#delivery-phases).

## The problem

Today each agent that needs Bugzilla ships a `broker` sidecar holding a real BMO API key
([security.md](security.md)). That key is a single static credential: every run using a
given agent gets exactly the same access, and the only reason it is safe is that the
account behind it can read nothing confidential.

Several things we want to build need private bugs. A triage or fix agent pointed at a
security bug cannot read it. A security-bug de-duplication agent cannot search the corpus
it would need to search. Widening the broker's key is not an option: it would give every
run of that agent standing access to everything the account can see, with no per-run limit,
no audit trail beyond BMO's own, and no way to revoke one run.

So: a per-run capability instead of a static key.

## Two components, two names

| Name             | Where              | Holds                 | Speaks        |
| ---------------- | ------------------ | --------------------- | ------------- |
| `broker`         | sidecar in the run | the run's scope token | MCP           |
| `bugzilla-proxy` | shared service     | the BMO credential    | Bugzilla REST |

The broker keeps its existing job (hosting the MCP tool server, keeping credentials out of
the agent container) but its credential becomes a short-lived, run-scoped token rather than
a BMO key. The proxy is new: it holds the credential, evaluates what a token is allowed to
see, filters content, and logs every access.

```
hackbot-api ──mint scoped JWT (signed as its own SA)──┐
     │                                       │
     └─ POST /agents/{a}/runs                ▼
             │                     bugzilla-proxy (Cloud Run, IAM-restricted)
             ▼                     holds the BMO credential, verifies the JWT,
   Cloud Run Job task              evaluates scope, filters content, audit-logs
   ┌──────────────────────┐                  │
   │ agent    (no creds)  │                  ▼
   │   ↕ loopback MCP     │              BMO /rest
   │ broker (holds JWT) ──┼──────────────────┘
   └──────────────────────┘   IAM + scope JWT: two independent gates
```

The credential lives in one hardened deployment rather than in every agent task, which
gives one place to revoke, one audit log, and rate limiting that can see enumeration
across runs. The cost is a network hop and a service to operate.

## The token

hackbot-api mints at trigger time and signs through IAM's `signJwt`, as **its own service
account**. The signing key is Google-managed: nothing in either service generates, holds,
exchanges or rotates key material. Google publishes each account's public certificates at a
URL derived from its email, so the proxy's entire trust configuration is one string, the
expected issuer's email, and fetching certs from that account's URL is itself the issuer
binding.

This also needs no new IAM. `signJwt` comes from `roles/iam.serviceAccountTokenCreator`,
which hackbot-api already holds **on itself** for the GCS signed-policy path
([security.md](security.md)).

The one constraint it adds: **IAM will not sign a JWT more than 12 hours out**. The default
8 hour job timeout plus its grace window fits with room to spare, but `mint` checks the sum
and fails at trigger time with a message naming the settings involved, rather than letting
IAM reject it with something that reads like a permissions problem.

The proxy verifies and holds no per-run state: the token is the entire policy.

```json
{
  "iss": "hackbot-api@<project>.iam.gserviceaccount.com",
  "aud": "hackbot-bugzilla-proxy",
  "sub": "run:<run_id>",
  "jti": "...",
  "exp": "<job timeout + grace>",
  "agent": "security-dedup",
  "requested_by": "someone@mozilla.com",
  "bz": {
    "read_only": true,
    "confidential": true,
    "grants": [
      {
        "tier": "metadata",
        "anchor": {
          "groups": ["core-security"],
          "product": ["Core", "Firefox"],
          "created_after": "2019-01-01"
        },
        "fields": [
          "id",
          "summary",
          "product",
          "component",
          "status",
          "resolution",
          "dupe_of",
          "keywords",
          "creation_time"
        ],
        "endpoints": ["bug"]
      },
      {
        "tier": "full",
        "anchor": { "static_bugs": [1899123] },
        "endpoints": ["bug", "bug/*/comment"]
      }
    ],
    "promotions_max": 10,
    "attachments": false,
    "filter_content": "llm"
  }
}
```

The scope template lives server-side on `AgentSpec`, beside `auto_apply_actions`, with
per-run substitution of the target bug. **A caller never sends a scope.** That keeps scope
server-determined even as scopes get expressive.

The token travels in `X-Bugzilla-API-Key`, where bugsy already puts `api_key`, so it must
stay small enough for a header: broad anchors are fine, long `static_bugs` lists are not.

### Why this is not a Google credential

Signing as a service account raises a fair question: the token carries hackbot-api's own
signature, so could a run present it to Google and act as hackbot-api? Google accepts a
self-signed service account JWT in two flows, and this token satisfies neither:

| Flow                                      | Google requires                               | This token has                               |
| ----------------------------------------- | --------------------------------------------- | -------------------------------------------- |
| Exchange at `oauth2.googleapis.com/token` | `aud` = that URL, a `scope` claim, `exp` ≤ 1h | `hackbot-bugzilla-proxy`, none, ~8h          |
| Direct call to `SERVICE.googleapis.com`   | `aud` = that API, `sub` == `iss`              | `hackbot-bugzilla-proxy`, `sub` = `run:<id>` |

Four independent mismatches, any one of which is disqualifying. But note what this rests
on: **the claims, not the mechanism.** The signature really is hackbot-api's service
account, so an audience pointing at a Google endpoint would turn a scoped Bugzilla read
token into full impersonation of this service, handed to the least-trusted container in the
system.

Which is why **the audience is a constant in code, not a setting**
(`bz_token.TOKEN_AUDIENCE`, and `tokens.TOKEN_AUDIENCE` on the other side). Nothing
legitimate varies it, since environments are separated by the signing account, so there is
no deploy-time value to get wrong. Validating a configurable audience would have left the
mistake possible and merely caught it late.

The certificate URL (`tokens.CERTS_URL_TEMPLATE`) is a constant for a sharper version of
the same reason. It decides which signatures the proxy accepts, so pointing it at a host
someone else controls is not a degradation, it is a complete authentication bypass: whoever
answers gets to mint runs. Google's endpoint is fixed, environments differ only in which
account is substituted into it, and local runs verify against a PEM without fetching at
all.

Since both are literals duplicated across two packages that cannot import each other, the
proxy's tests parse hackbot-api's module and assert the audiences match, so drift fails in
unit tests rather than at the first real request.

This is the one place the KMS alternative was safer by construction: a key we own is not a
credential Google would ever accept, whatever we put in the payload.

### Delivery

`jobs.py` gains a second `ContainerOverride` targeting the `broker` container, carrying
only the token. That relaxes today's rule that per-execution overrides touch only the
`agent` container, so it needs a guard: the API allowlists exactly which variable names it
may set on `broker`, and the value is minted server-side, never derived from caller input.

The obvious alternative, having the broker fetch its own token using its Google identity
the way [anthropic_wif.py](../../libs/hackbot-runtime/hackbot_runtime/anthropic_wif.py)
does, does not work as stated: **every container in a Cloud Run task shares one service
account**, so the agent container could mint the same token. It becomes viable only with a
one-shot mint endpoint consumed during ordered broker startup, which is where to go if 8
hour token lifetimes become uncomfortable.

### Client side

`Bugsy(api_key=..., bugzilla_url=...)` with no username makes no network call at
construction and exposes `.session`, so the broker needs no bugsy patching:

```python
client = bugsy.Bugsy(api_key=scope_token, bugzilla_url=settings.bugzilla_proxy_url)
client.session.auth = GoogleIdTokenAuth(audience=settings.bugzilla_proxy_url)
```

`agent_tools/bugzilla.py` already renders proxy codes 101 (endpoint not exposed) and 102
(access denied) as structured `ToolError`s, so agents already know how to handle a bug
falling outside scope. No tool or context change is needed.

## The scope model

Access to private-capable agents is limited to a small allowlist of people who already
have access to all private bugs. That removes the confused-deputy problem: a token can
never hand its holder something they lack.

The scope therefore is **not** an authorization boundary against the requester. It is still
worth keeping tight, for four other reasons:

- **Data minimization.** Every bug the scope admits can reach the model context,
  `summary.json`, GCS and traces. The requester's clearance does not shrink that footprint.
- **Blast radius.** The token sits in a container beside model-directed code for hours.
- **Provably public agents.** A token that structurally cannot express private scope is
  easier to reason about than a policy check.
- **Audit precision.**

### Structural anchors and narrowing filters

Rules divide by who controls the underlying field:

| Class             | Fields                                                                         | Role               |
| ----------------- | ------------------------------------------------------------------------------ | ------------------ |
| Structural anchor | `static_bugs`, `product`, `component`, `creation_time`, `status`, `resolution` | may grant          |
| Narrowing filter  | `keywords`, `whiteboard`, `blocks`                                             | may only intersect |

Every private-scope grant must carry at least one structural anchor, and narrowing filters
may only intersect it, never union into it. Every configured rule must hold: a grant is an
AND across its rules, never an OR, so adding one can only ever shrink what it admits.

The point is that narrowing fields are editable by anyone with `editbugs`. With the
requester allowlist that is no longer an escalation path, but it is still an uncontrolled
way for a third party to enlarge a confidential run's footprint. Anchoring on
`product = Core AND groups ⊆ {core-security}` cannot be widened by editing a whiteboard.

`groups` sits in the anchor on the wire but is not one of these rules. It is a **ceiling**:
a bug in any group the grant does not name is denied, and a public bug matching the other
rules is served whatever it contains. An empty `groups` therefore means public bugs only,
which is what makes private access opt-in rather than opt-out. A bug whose `groups` the
proxy cannot see is denied outright, so the field is added to every upstream request and
stripped again on the way out.

### Field tiers

De-duplication needs summary-level fields across a large corpus and full detail for a
handful of finalists, so a grant carries a tier:

- **metadata**: id, summary, product, component, status, resolution, dupe_of, keywords,
  creation_time. Comment and attachment endpoints denied.
- **full**: everything the endpoints allow.

Three benefits fall out at once. The model context holds many summaries and few full texts.
`filter_content: llm` runs per comment, and the metadata tier serves no comments, so
corpus-wide access costs nothing in model calls. And candidate generation needs no per-bug
comment fetch.

A run may promote a corpus bug to full tier up to `promotions_max` times, each logged. This
grants no new authority, since the bug was already in the corpus; it makes broad reads
deliberate. With multiple proxy instances the counter is per-instance, so this is a soft
limit and an audit signal, not a hard cap.

### Search is the primary path

Under a corpus scope the agent works through `search_bugs`, not `get_bugs`. Post-filtering
results is correct but not sufficient: the proxy must **inject the scope predicate into the
upstream query**, or it pages through BMO to discard most of what it fetches. Post-filtering
remains the authority; query rewriting is what makes it usable.

Search is also where existence is disclosed, so the audit log records the query and the
returned ids, not only per-bug fetches.

## Who may request private scope

Two allowlists, both in hackbot-api: which agents may ever receive private scope, and which
requesters may trigger them.

This makes requester identity the entire security boundary, and today it does not hold up.
The UI derives the email server-side from the session
([app/api/runs/route.ts](../../services/hackbot-ui/app/api/runs/route.ts)), but hackbot-api
accepts `X-On-Behalf-Of` as a plain header from any caller holding `X-API-Key`
([routers/runs.py](../../services/hackbot-api/app/routers/runs.py)), and that key is shared
between the UI, the pulse listener and scripts. Anyone holding it can assert any identity.

So before private access ships: split `X-API-Key` per caller class, let only a UI-class key
assert `X-On-Behalf-Of`, and give automated callers keys that cannot reach private-capable
agents.

The requester allowlist also needs periodic reconciliation against real BMO group
membership, with a named owner, so it does not go stale.

## Containment

Granting the read is the smaller half. Once a run touches a private bug, everything
downstream carries it: `summary.json` and artifacts in GCS, the UI (today any
`@mozilla.com` session sees any run), traces, Cloud Logging, notification emails, and above
all the recorded actions. `slack.post_message`, a public `phabricator.submit_patch` and a
try push are each a direct path from a security bug to a public artifact.

A `confidential` flag is set on the run **at mint time**, since the API has already
resolved the scope before the job starts, and propagates to:

| Surface                                         | Behaviour when confidential |
| ----------------------------------------------- | --------------------------- |
| UI                                              | run and artifacts gated     |
| Tracing                                         | disabled, or scrubbed       |
| `bugzilla.add_comment`                          | `is_private` forced true    |
| `slack.post_message`                            | refused by the applier      |
| `phabricator.submit_patch` to a public revision | refused                     |
| try pushes                                      | blocked                     |

The proxy refuses to serve private data to a token not marked confidential, so the two
cannot drift apart. The agent is also told in its prompt, though that is mitigation, not
enforcement.

## Content filtering

Comments and attachments from untrusted authors can carry prompt injection, so the token
names how to handle them: `off` relays unchanged, `remove` blanks everything from authors
outside a set of trusted groups, and `llm` classifies each piece and blanks only what is
flagged.

`remove` is the cheap default, but it is the wrong one for security bugs, where the
reporter is very often an external researcher: it would blank exactly the content the agent
needs. Private-scoped tokens realistically need `llm`, which adds an Anthropic dependency to
the proxy. Injection risk stays real in both directions: untrusted content flows in, and
confidential content must not flow out.

## Deployment

- Cloud Run service, own least-privilege service account, `--no-allow-unauthenticated`.
- `run.invoker` granted only to the agent Job service accounts.
- **Ingress `all`, not `internal`.** Cloud Run _jobs_ egress to the internet by default, so
  `internal` would block them unless every agent job also gets Direct VPC egress with
  all-traffic routing. IAM auth is what restricts callers; Cloud Run's front end rejects
  unauthenticated requests before any of our code runs. VPC-internal is a later hardening
  step, and this is the detail most likely to cost a day if assumed to work out of the box.
- BMO credential in Secret Manager, `secretAccessor` to the proxy service account only.
- No signing key to provision: hackbot-api signs as its own service account, and the
  proxy is configured with that account's email as `token_issuer`. The only IAM needed
  is `roles/iam.serviceAccountTokenCreator` on itself, which hackbot-api already has.
- The proxy needs egress to `www.googleapis.com` to fetch the issuer's certificates.

## Delivery phases

**0. Proxy in the path, public scope only. Built.** What landed:

| Piece                                               | Where                                                                              |
| --------------------------------------------------- | ---------------------------------------------------------------------------------- |
| Scope evaluation: anchors, tiers, the group ceiling | [bugzilla_proxy/scope.py](../../services/bugzilla-proxy/bugzilla_proxy/scope.py)   |
| Token verification and the claim shapes it refuses  | [bugzilla_proxy/tokens.py](../../services/bugzilla-proxy/bugzilla_proxy/tokens.py) |
| Endpoint allowlist, post-filtering, audit log       | [bugzilla_proxy/app.py](../../services/bugzilla-proxy/bugzilla_proxy/app.py)       |
| Minting, scope templates, the `$bug_id` placeholder | [hackbot-api/app/bz_token.py](../../services/hackbot-api/app/bz_token.py)          |
| The `broker` container override and its allowlist   | [hackbot-api/app/jobs.py](../../services/hackbot-api/app/jobs.py)                  |

Not yet done in this phase, in order:

1. Deploy the Cloud Run service and its IAM, and set `token_issuer` to hackbot-api's
   service account email.
2. Test it against real BMO traffic with a hand-minted token.
3. **Then** onboard the first agent: point its broker's bugsy client at the proxy (see
   [Client side](#client-side)) and set `bugzilla_scope` on its `AgentSpec`.

Nothing outside this table has changed, deliberately. Nothing in
[agents/](../../agents/) is touched, and no shared client helper exists yet: what a broker
needs depends on whether the deployed service ends up behind IAM invoker auth, which step 1
settles. Writing that helper before then would be guessing at its shape.

Because onboarding is opt-in per agent and minting is skipped entirely when no signing key
is configured, the rollback at every step is to stop minting rather than to revert code.

Deliberately deferred out of phase 0: content filtering (a token asking for `remove` or
`llm` is refused rather than served unfiltered), upstream query rewriting (results are
filtered after they arrive, so a capped search can return fewer rows than asked for), and
tier promotion.

**1. Trustworthy requester identity.** Split `X-API-Key` per caller class. Small, and
phases 3 and 4 rest on it.

**2. Confidential runs.** The flag and every enforcement point in the table above. The
largest phase, and independent of the proxy, so it can run in parallel with 0 and 1. Done
when a synthetic confidential run demonstrably fails on each path.

**3. Private access, explicit ids.** The `groups` rule with deny by default, the requester
allowlist, the privileged BMO credential, one agent on one pilot group. Done when a token
without a private grant gets a clean 102 the agent handles, and one with a grant succeeds
on a run marked confidential.

**4. Corpus scope.** Structural anchors, field tiers, query rewriting, promotion budget.
This unblocks de-duplication. Done when a bug outside the anchor provably never appears in
search results, and metadata-tier bugs provably cannot yield comments.

`test-repair` reaches Bugzilla through an injected `BUGZILLA_MCP_URL` rather than a broker,
so it needs the same treatment separately.

## Long-lead items

These gate later phases and should start during phase 0:

- The privileged BMO account: which group memberships, who owns it, MozillaSecurity and BMO
  team approval. Longest lead time, gates phase 3.
- Who is on the requester allowlist, and who maintains it.
- Confirmation that Anthropic data handling covers security bug content.
- GCP provisioning: the proxy's own service account and Cloud Run service.
- Booking the security review.

## Open questions

- Is `groups` reliably present and complete on BMO search results? Anchor evaluation depends
  on it, and if a search can return a bug without its groups the proxy must fail closed and
  re-fetch.
- What corpus does de-duplication actually need: all of `core-security` across products, or
  per-product? This sets the footprint.
- Does de-duplication need comment 0 for every candidate? That decides whether two tiers
  suffice or a third is needed.
- Delegated credentials (the proxy using the requester's own BMO credential) are less
  necessary given the allowlist, but retain two advantages: access revokes automatically
  when someone leaves a group, and BMO-side audit attributes reads to a person. Worth a
  sentence to the BMO team rather than a redesign.
