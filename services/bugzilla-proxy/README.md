# bugzilla-proxy

An authorization proxy for the Bugzilla REST API. It holds the upstream
credential; callers present a per-run capability token saying what they may
read.

Design and rollout plan: [docs/hackbot/bugzilla-proxy.md](../../docs/hackbot/bugzilla-proxy.md).

## What it does

- Verifies an RS256 capability token minted by hackbot-api, against the public
  certificates Google publishes for hackbot-api's service account (or a static
  PEM locally). No key material is provisioned or exchanged between the two
  services: the shared configuration is one email address.
- Exposes exactly four read endpoints. Everything else, and every write, is a
  Bugzilla-shaped 101 "endpoint not exposed".
- Decides access per bug from the bug's own fields, fetched with the proxy's
  credential. A bug in a security group is denied unless the token names that
  group, and a bug whose groups it cannot see is denied outright.
- Projects each bug down to the fields the token's tier exposes.
- Logs every decision with the run, agent and requester behind it.

## What it does not do yet

Phase 0 of the plan. Not implemented: content filtering (`filter_content` other
than `off` is refused rather than silently ignored), upstream query rewriting
(search results are filtered after they arrive, so a capped search can return
fewer rows than the caller asked for), and promotion between tiers.

## Endpoints

| Path                            | Serves                                |
| ------------------------------- | ------------------------------------- |
| `GET /rest/bug`                 | Search, and bulk fetch by `id`        |
| `GET /rest/bug/{id}/comment`    | A bug's comments                      |
| `GET /rest/bug/{id}/attachment` | A bug's attachments                   |
| `GET /rest/bug/attachment/{id}` | One attachment, authorized by its bug |
| `GET /healthz`                  | Liveness, no token required           |

## Configuration

All settings take a `BUGZILLA_PROXY_` prefix.

| Variable                     | Purpose                                                           |
| ---------------------------- | ----------------------------------------------------------------- |
| `UPSTREAM_URL`               | Bugzilla REST base URL                                            |
| `UPSTREAM_API_KEY`           | The credential, from Secret Manager                               |
| `TOKEN_ISSUER`               | hackbot-api's service account email, whose certs verify the token |
| `JWT_PUBLIC_KEY`             | A PEM instead, for local runs. Exactly one of these two           |
| `DECISION_CACHE_TTL_SECONDS` | How long bug metadata is reused (default 300)                     |
| `MAX_SEARCH_LIMIT`           | Ceiling on rows fetched per search (default 500)                  |

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
openssl genrsa -out /tmp/bzproxy.pem 2048
openssl rsa -in /tmp/bzproxy.pem -pubout -out /tmp/bzproxy.pub

BUGZILLA_PROXY_UPSTREAM_API_KEY=... \
BUGZILLA_PROXY_JWT_PUBLIC_KEY="$(cat /tmp/bzproxy.pub)" \
  uv run --package bugzilla-proxy python -m bugzilla_proxy.main
```

Point hackbot-api at the same keypair with `BZ_TOKEN_PRIVATE_KEY`, and a broker
at the proxy with `BUGZILLA_PROXY_URL` plus `BUGZILLA_PROXY_AUDIENCE=""` (the
local proxy is not behind IAM, so there is no identity token to present).

## Tests

```bash
uv run --package bugzilla-proxy pytest
```

`tests/test_scope.py` is the one to read first: it is where the refusals live.
