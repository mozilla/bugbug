# bugzilla-proxy

An authorization proxy for the Bugzilla REST API. It holds the upstream
credential; callers present a per-run capability token saying what they may
read.

Design and rollout plan: [docs/hackbot/bugzilla-proxy.md](../../docs/hackbot/bugzilla-proxy.md).

## Two keys

The proxy holds two Bugzilla keys and picks one per token, from its scope:

| Scope                                     | Reads with                      |
| ----------------------------------------- | ------------------------------- |
| No grant names a security group           | `BUGZILLA_UNPRIVILEGED_API_KEY` |
| At least one grant names a security group | `BUGZILLA_PRIVILEGED_API_KEY`   |

The unprivileged key belongs to an account in no groups, so for every
public-only scope BMO refuses anything private even if a check in the proxy
were wrong. The proxy's checks apply either way, so a security bug the
unprivileged account happens to see, say because it was cc'd, is still denied.
The key never changes what a request may look like: switching an agent between
public and private scopes is a change to its template, nothing on its side.

Both keys send `BUGZILLA_EDGE_KEY` as `Mozilla-Edge-Key`, which exempts the
proxy from BMO's edge rate limiting. The proxy does not yet apply a limit of
its own.

## What it does

- Verifies an RS256 capability token minted by hackbot-api, against the public
  certificates Google publishes for hackbot-api's service account (or a static
  PEM locally). No key material is provisioned or exchanged between the two
  services: the shared configuration is one email address.
- Exposes exactly four bug endpoints, plus `version` for brokers' startup
  probe. Everything else, and every write, is a Bugzilla-shaped 101 "endpoint
  not exposed".
- Decides access per bug from the bug's own fields, fetched with the proxy's
  credential. A bug in a security group is denied unless the token names that
  group, and a bug whose groups it cannot see is denied outright.
- Projects each bug down to the fields the token's tier exposes.
- Accepts only an allowlist of query parameters on the per-bug endpoints,
  refusing anything else, and cuts responses down to what it authorized. BMO
  merges query parameters into the call a path maps to, so `bug/1/comment?ids=2`
  would otherwise return bug 2's comments after checking only bug 1. A comment
  request's `ids` is still supported: each bug in it is authorized first.
- Refuses `comments`, `attachments`, `history` and `description` in search;
  their own endpoints are the only way to read them.
- Drops comments and attachments marked `is_private` (or missing the field)
  unless the grant serving the bug is cleared for private data.
- Logs every decision with the run, agent and requester behind it.

## What it does not do yet

Phase 0 of the plan. Not implemented: content filtering (`filter_content` other
than `off` is refused rather than silently ignored), full upstream query
rewriting (a public-only scope asks BMO to leave out bugs in security groups,
but anchors are still applied after results arrive, so a search can return
fewer rows than its `limit` even when more matching bugs are in scope), and promotion between tiers.

## Endpoints

| Path                            | Serves                                |
| ------------------------------- | ------------------------------------- |
| `GET /rest/bug`                 | Search, and bulk fetch by `id`        |
| `GET /rest/bug/{id}/comment`    | A bug's comments                      |
| `GET /rest/bug/{id}/attachment` | A bug's attachments                   |
| `GET /rest/bug/attachment/{id}` | One attachment, authorized by its bug |
| `GET /rest/version`             | BMO's version, for any valid token    |
| `GET /health`                   | Liveness, no token required           |

## Errors

Errors have Bugzilla's shape (`error`, `code`, `message`), so the agents' client
reads them unchanged.

| Code | Status  | Meaning                                                         |
| ---- | ------- | --------------------------------------------------------------- |
| 101  | 404/405 | Endpoint not exposed, or a write                                |
| 102  | 401/404 | Token rejected (the message says why), or bug/attachment denied |
| 103  | 502/503 | Bugzilla, or the token issuer's keys, could not be reached      |
| 104  | 400     | The request asks for something the proxy will not do as written |

Two rules decide which:

- **Refuse, don't adjust.** A request the proxy cannot honour as written is a
  104 saying what to change: a `limit` out of range, an unsupported parameter,
  a credential in the query string, or a field the token cannot return. The
  proxy never quietly caps, drops or rewrites a request.
- **A denial reveals no more than BMO would.** The proxy's credential can see
  more than the caller, so every reason a bug or attachment is unavailable gets
  the same message, naming only what the caller asked for. The reason goes to
  the audit log. Filtering stays silent only where BMO's is: search leaves out
  bugs the caller cannot see, and comment and attachment lists leave out
  private entries.

## Configuration

| Variable                        | Purpose                                                               |
| ------------------------------- | --------------------------------------------------------------------- |
| `BUGZILLA_API_URL`              | Bugzilla REST base URL                                                |
| `BUGZILLA_PRIVILEGED_API_KEY`   | Reads for scopes with a private grant                                 |
| `BUGZILLA_UNPRIVILEGED_API_KEY` | Reads for every other scope; an account in no groups                  |
| `BUGZILLA_EDGE_KEY`             | Sent as `Mozilla-Edge-Key`, exempting the proxy from BMO's rate limit |
| `TOKEN_ISSUER`                  | hackbot-api's service account email, whose certs verify the token     |
| `JWT_PUBLIC_KEY`                | A PEM instead, for local runs. Exactly one of these two               |
| `DECISION_CACHE_TTL_SECONDS`    | How long bug metadata is reused (default 300)                         |
| `MAX_SEARCH_LIMIT`              | Largest `limit` a search may ask for, and its default (500)           |

All three keys come from Secret Manager. The two API keys must differ, and the
proxy refuses to start if they do not: the unprivileged key is the backstop
for public-only scopes, so it must not be able to see anything private. Either
may be left unset; a token needing the missing one gets a 503 saying so.

Deployed, `TOKEN_ISSUER` is the whole trust configuration. The certificate URL is
derived from it, so only that account's keys are ever candidates for verifying a
token.

Two values are deliberately **not** configurable, and live as constants in
`bugzilla_proxy/tokens.py` instead:

- `TOKEN_AUDIENCE`, which must match what hackbot-api mints, and which would
  make the token a Google credential if pointed at a Google endpoint.
- `CERTS_URL_TEMPLATE`, which decides whose signatures we accept. Pointing it
  elsewhere would let whoever answers it authenticate as any run.

Neither varies legitimately, so neither is something a deploy can get wrong. The
design doc covers the reasoning under "Why this is not a Google credential".

## Running locally

```bash
uv sync --extra dev --package bugzilla-proxy
openssl genrsa -out /tmp/bugzilla-proxy-dev.pem 2048
openssl rsa -in /tmp/bugzilla-proxy-dev.pem -pubout \
  -out /tmp/bugzilla-proxy-dev.pub

BUGZILLA_PRIVILEGED_API_KEY=... \
BUGZILLA_UNPRIVILEGED_API_KEY=... \
JWT_PUBLIC_KEY="$(cat /tmp/bugzilla-proxy-dev.pub)" \
  uv run --package bugzilla-proxy python -m bugzilla_proxy.main
```

Point hackbot-api at the same keypair with `BZ_TOKEN_PRIVATE_KEY` so the tokens
it mints verify here.

No agent is wired to the proxy yet, so there is nothing else to configure. When
one is, its broker points its Bugzilla client at this URL and presents the run's
token; a local proxy is not behind IAM, so it needs no identity token.

## Tests

```bash
uv run --package bugzilla-proxy pytest
```

`tests/test_scope.py` is the one to read first: it is where the refusals live.
