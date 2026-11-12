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

Pins are optional, and set by hand: a shape change without them keeps each
environment's running image (an expand fits it; for a contract, ship the code
that stops needing what's removed first). To move a contract and its image
together, pin the build's images when generating the infra PR:

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
hackbot-deploy deploy  agents/bug-fix --env dev --agent-image …@sha256:… --broker-image …@sha256:…
hackbot-deploy deploy  services/hackbot-api --env dev --image …@sha256:…
hackbot-deploy promote agents/bug-fix --from stage --to prod
hackbot-deploy rollback services/hackbot-ui --env prod
```

Everything deployable is named by its path in the repository, which also says
what it is, so no list of names is kept anywhere: `agents/<name>` is an agent
with a `[deploy]` table, deployed as a Cloud Run Job, and `services/<name>` is
a service with its own `cloudbuild.yaml`, deployed as a Cloud Run service. A
service takes `--image`; an agent takes `--agent-image` (plus `--broker-image`
where it has a broker). Mixing them up is an error. Resource names keep their
own form (`hackbot-agent-bug-fix-dev`, `hackbot-api-dev`), since Cloud Run
names can't hold a slash.

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
4. An older build doesn't replace a newer one (`--force` to override; dev
   deploys always pass it). A build waits for older builds of its trigger
   (`wait-turn`), but a manual deploy can still race one, so this matters.
5. The Job's containers match the images given, and its config-hash label
   agrees with `applied.json` (catching an incomplete apply).

A service deploy skips the gate (2, 3 and 5): its infrastructure is written by
hand in webservices-infra and changes there first. It changes only the image,
and fails without recording anything if the new revision never becomes ready,
in which case Cloud Run keeps serving the old one.

Then it updates the Job or service, reads it back, and writes
`deployments/<id>.json` (`deployments/agents/bug-fix.json`) with the images,
version, commit, build time and a deployment history. The write is conditioned on the record's generation, so
two concurrent deploys can't silently overwrite each other. A rollback steps
back through that history one deployment at a time.

Exit codes: `0` done or nothing to do, `1` failed, `3` waiting for infra (the
image's shape isn't applied in that environment). The build configs treat `3`
as a failure: apply the infra PR, then rerun.

The environment names (projects, the `-dev`/`-stage` suffix, bucket names)
mirror the Terraform in webservices-infra. Change one, change both.

## Prod

Prod is never deployed by a build, and never without an approval. From the
Actions tab, the **Hackbot prod deploy** workflow's `promote` compares stage
with prod for one agent or service (`promotion`), and if they differ, waits for
a reviewer in the GitHub environment for its kind: `hackbot-prod-agents` or
`hackbot-prod-services`. On approval it runs `promote --expect`, which puts on
exactly the digests that were planned, or reports `superseded` if stage has
moved on. The approver is recorded as `deployed_by`. For a service, it also
marks its Sentry release (`<service>@<version>`) deployed to production, with
the `SENTRY_AUTH_TOKEN` secret of `hackbot-prod-services`.

`rollback` steps prod back through its history in the `hackbot-prod-rollback`
environment. The prod deploy account is bound to those three environments, so no workflow can
deploy prod without passing their protection. A shape change reaches prod by
applying the infra PR's prod directory instead, which puts on the images pinned
for it.

## Building and releasing

A release is a version bump: the `VERSION` file of an agent
(`agents/<agent>/VERSION`) or a service (`services/<service>/VERSION`), which
its package also reads. Images are tagged with their commit and labelled with
their version (`org.opencontainers.image.version`).
Six Cloud Build triggers, created in webservices-infra, run the build configs:

| trigger                                 | runs on                                                         | deploys                                                                         |
| --------------------------------------- | --------------------------------------------------------------- | ------------------------------------------------------------------------------- |
| `stage-agents`                          | the default branch, touching `agents/*/VERSION`                 | to stage, every agent whose version isn't the one stage runs, one after another |
| `stage-hackbot-api`, `stage-hackbot-ui` | the default branch, touching `services/<service>/VERSION`       | to stage, if its version isn't the one stage runs                               |
| `dev-agents`                            | `dev/agents/<agent>` or `dev/agents/<agent>/<anything>`         | that agent to dev, always                                                       |
| `dev-hackbot-api`, `dev-hackbot-ui`     | `dev/services/<service>` or `dev/services/<service>/<anything>` | the service to dev, always                                                      |

```sh
git push -f upstream HEAD:dev/agents/bug-fix               # try a branch in dev
git push -f upstream HEAD:dev/services/hackbot-api/my-fix  # a suffix keeps branches apart
```

Dev is a shared sandbox: the last push wins. The branches have to be in
mozilla/bugbug, not a fork.

A stage trigger run by hand deploys regardless of version: `stage-agents`
releases every agent, or just those in `_AGENTS=bug-fix,test-repair`, and a
service's trigger redeploys the service. A newer build waits for older builds
of the same trigger (`wait-turn`).

The tool makes every decision; the build configs only build images and call
it. `release-plan` writes what to release (in dev, what the branch names;
otherwise each agent's, or the named service's, package version against the
environment's record, or everything on a run by hand), one `name version
previous-commit config-hash broker` line each. `release` deploys those whose
images built, one after another: one failing doesn't stop the others, and it
fails at the end, naming them; a rerun only redoes those. The services' builds
also make the Sentry release `<service>@<version>` (dev builds add
`+<commit>`), with the commits since what the environment ran before, and mark
its deploy there.
