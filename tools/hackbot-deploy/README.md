# hackbot-deploy

Deploy tooling for hackbot: the `[deploy]` table in each agent's
`hackbot.toml`, the generator that turns those tables into `agents.json` for
mozilla/webservices-infra, and the script that ships images for agents and for
the API and UI.

The split it enforces: **Terraform owns an agent's infrastructure, this owns
which image runs on it.** Changing an agent's code ships without touching the
infra repo. Changing its `[deploy]` table changes its shape, which goes through
an infra PR first.

## The `[deploy]` table

```toml
[deploy]
runtime = "cloud_run_job"   # the only runtime so far
cpu = "6"
memory = "24Gi"
timeout_seconds = 7200      # default

[deploy.env]                # optional static environment
SOME_SETTING = "value"

[deploy.secret_env]         # optional: env var -> Secret Manager secret *name*
SOME_KEY = "some-secret"

[deploy.workspace]          # optional disk-backed /workspace
size = "40Gi"

[deploy.broker]             # optional credential sidecar
cpu = "1"                   # default
memory = "512Mi"            # default

[deploy.broker.secret_env]  # optional, on top of the shared broker secrets
SOME_OTHER_KEY = "some-other-secret"
```

Unknown keys are errors, so a typo fails instead of falling back to a default.
An agent with no `[deploy]` table isn't deployed and is left out of the
manifest. The runtime ignores the table (`libs/hackbot-runtime` has a test
pinning that).

Any secret named here must be readable by the agent account in
webservices-infra (`local.secret_readers`); its plan fails otherwise.

## Generating `agents.json`

```sh
uv run --package hackbot-deploy python -m hackbot_deploy manifest \
    --previous ../webservices-infra/hackbot/tf/modules/resources/agents.json \
    --output ../webservices-infra/hackbot/tf/modules/resources/agents.json
```

`--previous` is required in practice: each agent's `compatible` list (the
shapes a rollback can still reach) is carried forward from it. The summary on
stderr classifies every agent:

| change                            | meaning                                                            | rollback                         |
| --------------------------------- | ------------------------------------------------------------------ | -------------------------------- |
| `expand`                          | sizing, timeout, added env, added workspace                        | still possible onto older images |
| `contract`                        | runtime changed, broker added or removed, env or workspace removed | cut off                          |
| `added` / `removed` / `unchanged` | as named                                                           |                                  |

A removal is a contract because an image built for the old shape may read
what's gone. A broker either way changes the container set. Ship a removal in
two releases: first stop depending on it, then remove it.

`config_hash` is sha256 over the canonical JSON of the entry (sorted keys, no
whitespace, UTF-8) with the agent's name added and `config_hash`/`compatible`
removed, first 16 hex characters. webservices-infra documents the same
algorithm; the two must not drift. The file is written with 4-space indent and
non-ASCII escaped, exactly as that repo's `pretty-format-json` hook wants.

## Deploying

```sh
hackbot-deploy deploy  bug-fix --env dev --agent-image …@sha256:… --broker-image …@sha256:…
hackbot-deploy deploy  hackbot-api --env dev --image …@sha256:…
hackbot-deploy promote bug-fix --from stage --to prod
hackbot-deploy rollback hackbot-ui --env prod
```

`hackbot-api` and `hackbot-ui` are services and take `--image`; anything else
is an agent and takes `--agent-image` (plus `--broker-image` where it has a
broker). Mixing them up is an error.

(`uv run --package hackbot-deploy python -m hackbot_deploy …` from the repo
root.) Credentials are Application Default Credentials, so the same command
works from Cloud Build, GitHub Actions and a laptop.

What an agent deploy checks, in order:

1. Images are pinned by digest, never a tag.
2. The images carry `hackbot.config_hash` and agree on it.
3. **The gate.** A forward deploy needs that hash to equal the shape applied in
   the environment (`applied.json` in its registry bucket). A rollback needs it
   in `compatible`.
4. An older build doesn't replace a newer one (`--force` to override). Builds
   aren't serialised, so this matters.
5. The Job's containers match the images given, and its config-hash label
   agrees with `applied.json` (catching an incomplete apply).

A service deploy skips the gate (2, 3 and 5): its infrastructure is written by
hand in webservices-infra and changes there first. It changes only the image,
and fails without recording anything if the new revision never becomes ready,
in which case Cloud Run keeps serving the old one.

Then it updates the Job or service, reads it back, and writes
`deployments/<name>.json` with the images, commit, build time and a
deployment history. The write is conditioned on the record's generation, so
two concurrent deploys can't silently overwrite each other. A rollback steps
back through that history one deployment at a time.

Exit codes: `0` done or nothing to do, `1` failed, `3` waiting for infra. A
pipeline should treat `3` as "stop here", not as a failure;
`agents/cloudbuild.yaml` does.

The environment names (projects, the `-dev`/`-stage` suffix, bucket names)
mirror the Terraform in webservices-infra. Change one, change both.

## Building

`agents/cloudbuild.yaml` builds an agent's images with the hash label, and
`services/<service>/cloudbuild.yaml` builds the API and UI. Each then deploys
to dev and then stage. Their triggers are created in webservices-infra.
