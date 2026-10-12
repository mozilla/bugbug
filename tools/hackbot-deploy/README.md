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
There is no plain `env`: a constant belongs in the image's Dockerfile, and
anything that differs per environment comes from the variables Terraform gives
every agent. Only secrets need infrastructure.
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

| change                            | meaning                                                                                 | rollback                         |
| --------------------------------- | --------------------------------------------------------------------------------------- | -------------------------------- |
| `expand`                          | sizing, timeout, added secret, added workspace                                          | still possible onto older images |
| `contract`                        | runtime changed, broker added or removed, secret or workspace removed, secret repointed | cut off                          |
| `added` / `removed` / `unchanged` | as named                                                                                |                                  |

A removal is a contract because an image built for the old shape may read
what's gone. So is pointing an existing variable at a different secret: the
old image was never run against it. A broker either way changes the container
set. A contract still ships in one change, because the apply that moves an
environment to the new shape also puts on the images built for it (see
[Pinned images](#pinned-images)).

`config_hash` is sha256 over the canonical JSON of the entry (sorted keys, no
whitespace, UTF-8) with the agent's name added and the bookkeeping keys
(`config_hash`, `compatible`, `images`) removed, first 16 hex characters. webservices-infra documents the same
algorithm; the two must not drift. The file is written with 4-space indent and
non-ASCII escaped, exactly as that repo's `pretty-format-json` hook wants.

## Pinned images

An entry can carry `images`: the digests built for that shape, by container.
In each environment, Terraform leaves the images alone while the shape is the
one it has applied, and the deploy pipeline owns them. When an apply moves the
environment to a new shape (or creates the agent), it puts on the pinned images
in the same update, so the infrastructure and the image never disagree, not
even in prod before a promotion. Apply the PR one environment at a time (dev,
check, then stage, then prod) to control when each moves.

The manifest sync workflow fills them in once the build for a commit has
finished:

```sh
hackbot-deploy resolve-images bug-fix --tag <commit> \
    --repository us-docker.pkg.dev/moz-fx-hackbot-prod/hackbot-prod --output images.json
hackbot-deploy manifest --previous <infra main's agents.json> \
    --pending <the open bot PR's agents.json> --images images.json --output agents.json
```

`resolve-images` pins the build's `:<commit>` tags to digests, and writes `{}`
when the images were built for a shape the agent no longer has. `manifest`
pins images only for an agent whose shape differs from `--previous`; it keeps
what `--pending` pinned for the same shape, and drops them once the shape moves
on. An unchanged shape keeps what it had: a new image for it is the deploy
pipeline's to ship.

The deploy script notices when Terraform has put the pinned images on (the Job
runs `images` from `applied.json` but the record names something else) and
records that as a deployment by `terraform` before doing anything else, so
rollback and history include it.

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

1. Images are pinned by digest, never a tag, and are in Artifact Registry
   (`<region>-docker.pkg.dev`): reading their labels sends credentials to that
   host.
2. The images carry `hackbot.config_hash` and agree on it.
3. **The gate.** A forward deploy needs that hash to equal the shape applied in
   the environment (`applied.json` in its registry bucket). A rollback needs it
   in `compatible`.
4. An older build doesn't replace a newer one (`--force` to override). A build
   stops if an older build of its trigger is still running (`check-build`), but
   a manual deploy can still race one, so this matters.
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
