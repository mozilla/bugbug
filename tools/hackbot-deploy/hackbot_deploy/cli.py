"""Command line for hackbot deploys: agents, and the API and UI.

Exit codes: 0 done (or nothing to do), 1 failed, 3 waiting for infra. A build
pipeline should treat 3 as "skip the remaining environments", not as a failure.
"""

import json
import os
import sys
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any

import typer

from hackbot_deploy import deploy, manifest
from hackbot_deploy.gcp import GoogleCloud

EXIT_WAITING_FOR_INFRA = 3

app = typer.Typer(
    help=__doc__,
    no_args_is_help=True,
    add_completion=False,
    # Rich tracebacks in a terminal, plain ones in build logs (Cloud Build sets
    # no CI variable, so look for a terminal instead). Never print locals: they
    # might hold credentials.
    pretty_exceptions_enable=sys.stderr.isatty(),
    pretty_exceptions_show_locals=False,
)

Env = StrEnum("Env", sorted(deploy.ENVIRONMENTS))

AgentsDir = Annotated[Path, typer.Option(help="Where the agents live.")]
Name = Annotated[
    str,
    typer.Argument(
        help=f"An agent, or one of the services: {', '.join(deploy.SERVICES)}."
    ),
]
EnvOption = Annotated[Env, typer.Option("--env")]
DryRun = Annotated[bool, typer.Option("--dry-run")]


def _actor(cloud: GoogleCloud) -> str:
    """Who to record as deployed_by."""
    if os.environ.get("HACKBOT_DEPLOY_ACTOR"):
        return os.environ["HACKBOT_DEPLOY_ACTOR"]
    # GitHub Actions sets it; Cloud Build passes the pusher as it, and leaves
    # the payload binding unresolved when the trigger is run by hand.
    github = os.environ.get("GITHUB_ACTOR", "")
    if github and not github.startswith("$("):
        return f"github:{github}"
    if os.environ.get("BUILD_ID"):
        return f"cloudbuild:{os.environ['BUILD_ID']}"
    # Run by hand: whoever is signed in to Google.
    return cloud.identity() or os.environ.get("USER", "unknown")


def _report(result: deploy.Result) -> None:
    shape = f" ({result.config_hash})" if result.config_hash else ""
    print(f"{result.name} in {result.environment}: {result.action}{shape}")
    for container, image in sorted(result.images.items()):
        print(f"  {container}: {image}")


# Marks the generated `options:` list of the prod deploy workflow's name input.
_NAMES_MARKER = "# hackbot-deploy: names"


def set_workflow_names(text: str, names: list[str]) -> str:
    """Replace the options list that follows the marker comment with ``names``."""
    lines = text.splitlines(keepends=True)
    try:
        start = next(i for i, line in enumerate(lines) if _NAMES_MARKER in line) + 1
    except StopIteration:
        raise manifest.ManifestError(
            f"no '{_NAMES_MARKER}' line in the workflow"
        ) from None
    if start >= len(lines) or lines[start].strip() != "options:":
        raise manifest.ManifestError(
            f"'{_NAMES_MARKER}' must be followed by 'options:'"
        )
    indent = lines[start][: len(lines[start]) - len(lines[start].lstrip())] + "  "
    end = start + 1
    while end < len(lines) and lines[end].startswith(f"{indent}- "):
        end += 1
    return "".join(
        lines[: start + 1] + [f"{indent}- {name}\n" for name in names] + lines[end:]
    )


def _read_json(path: Path | None) -> Any:
    return json.loads(path.read_text()) if path else None


@app.command("manifest")
def manifest_(
    previous: Annotated[
        Path | None,
        typer.Option(help="The agents.json currently in webservices-infra."),
    ] = None,
    output: Annotated[
        str | None, typer.Option(help="Where to write it (default: stdout).")
    ] = None,
    check: Annotated[
        Path | None,
        typer.Option(help="Exit 1 if this file differs from what would be generated."),
    ] = None,
    pending: Annotated[
        Path | None,
        typer.Option(help="The open bot PR's agents.json, to keep images it pinned."),
    ] = None,
    images: Annotated[
        Path | None,
        typer.Option(
            help="JSON of images just built, {agent: {container: digest}}, as resolve-images writes."
        ),
    ] = None,
    agents_dir: AgentsDir = Path("agents"),
) -> None:
    """Generate agents.json from every agent's [deploy] table."""
    result, changes = manifest.build_manifest(
        manifest.load_configs(agents_dir),
        _read_json(previous),
        pending=_read_json(pending),
        images=_read_json(images),
    )
    text = manifest.dumps(result)
    for name, change in sorted(changes.items()):
        print(f"{change:>9}  {name}", file=sys.stderr)
    if check:
        if check.read_text() != text:
            print(f"{check} is out of date", file=sys.stderr)
            raise typer.Exit(1)
    elif output in (None, "-"):
        sys.stdout.write(text)
    else:
        Path(output).write_text(text)


@app.command()
def workflow_names(workflow: Path, agents_dir: AgentsDir = Path("agents")) -> None:
    """Fill a workflow's name choices with every deployable agent and service."""
    names = sorted(manifest.load_configs(agents_dir)) + list(deploy.SERVICES)
    text = workflow.read_text()
    updated = set_workflow_names(text, names)
    if updated != text:
        workflow.write_text(updated)
        print(f"{workflow}: updated the deployable names", file=sys.stderr)


@app.command()
def info(agent: str, agents_dir: AgentsDir = Path("agents")) -> None:
    """Print an agent's config hash and shape, shell-sourceable."""
    config = manifest.load_config(agents_dir / agent / "hackbot.toml")
    if config is None:
        print(f"{agent} has no [deploy] table", file=sys.stderr)
        raise typer.Exit(1)
    entry = config.manifest_entry()
    print(f"CONFIG_HASH={manifest.config_hash(agent, entry)}")
    print(f"RUNTIME={entry['runtime']}")
    print(f"HAS_BROKER={'true' if 'broker' in entry else 'false'}")


@app.command()
def resolve_images(
    agent: str,
    tag: Annotated[str, typer.Option(help="The tag the build pushed: its commit.")],
    repository: Annotated[
        str, typer.Option(help="host/project/repository the images are in.")
    ],
    output: Annotated[Path, typer.Option(help="Where to write the JSON.")],
    agents_dir: AgentsDir = Path("agents"),
) -> None:
    """Write {agent: {container: digest}} for the images a build pushed.

    Writes {} when they were built for a shape the agent no longer has.
    """
    config = manifest.load_config(agents_dir / agent / "hackbot.toml")
    if config is None:
        print(f"{agent} has no [deploy] table", file=sys.stderr)
        raise typer.Exit(1)
    entry = config.manifest_entry()
    images = deploy.built_images(
        GoogleCloud(),
        repository,
        agent,
        tag,
        manifest.config_hash(agent, entry),
        "broker" in entry,
    )
    if images is None:
        print(
            f"{agent}'s images at {tag} were built for an older shape; nothing to pin",
            file=sys.stderr,
        )
    output.write_text(json.dumps({agent: images} if images else {}, indent=2) + "\n")


@app.command()
def check_build(
    build_id: str,
    project: Annotated[str, typer.Option()],
    location: Annotated[str, typer.Option()],
) -> None:
    """Fail if an older build of this build's trigger is still running."""
    older = deploy.older_builds(GoogleCloud(), project, location, build_id)
    if older:
        print(
            f"an older build of this trigger is still running ({', '.join(older)}); "
            "rerun this trigger once it finishes to deploy this commit",
            file=sys.stderr,
        )
        raise typer.Exit(1)


@app.command("deploy")
def deploy_(
    name: Name,
    env: EnvOption,
    image: Annotated[
        str | None, typer.Option(help="A service's image, …@sha256:….")
    ] = None,
    agent_image: Annotated[
        str | None, typer.Option(help="An agent's image, …@sha256:….")
    ] = None,
    broker_image: Annotated[
        str | None, typer.Option(help="An agent's broker image, …@sha256:….")
    ] = None,
    force: Annotated[
        bool, typer.Option(help="Deploy even if a newer build is already running.")
    ] = False,
    dry_run: DryRun = False,
) -> None:
    """Deploy images to an environment."""
    cloud = GoogleCloud()
    common = {"force": force, "dry_run": dry_run, "actor": _actor(cloud)}
    if name in deploy.SERVICES:
        if agent_image or broker_image:
            raise deploy.DeployError(
                f"{name} is a service: use --image, not --agent-image/--broker-image"
            )
        if not image:
            raise deploy.DeployError(f"{name} is a service: --image is required")
        result = deploy.deploy_service(
            cloud, name, deploy.ENVIRONMENTS[env], image, **common
        )
    else:
        if image:
            raise deploy.DeployError(
                f"{name} is an agent: use --agent-image (and --broker-image), not --image"
            )
        if not agent_image:
            raise deploy.DeployError(f"{name} is an agent: --agent-image is required")
        images = {"agent": agent_image}
        if broker_image:
            images["broker"] = broker_image
        result = deploy.deploy_agent(
            cloud, name, deploy.ENVIRONMENTS[env], images, **common
        )
    _report(result)


@app.command()
def rollback(name: Name, env: EnvOption, dry_run: DryRun = False) -> None:
    """Roll back to the deployment before the current one.

    Takes no images: a rollback only steps back through the recorded history.
    """
    roll_back = (
        deploy.deploy_service if name in deploy.SERVICES else deploy.deploy_agent
    )
    cloud = GoogleCloud()
    _report(
        roll_back(
            cloud,
            name,
            deploy.ENVIRONMENTS[env],
            rollback=True,
            dry_run=dry_run,
            actor=_actor(cloud),
        )
    )


@app.command()
def promote(
    name: Name,
    source: Annotated[Env, typer.Option("--from")],
    target: Annotated[Env, typer.Option("--to")],
    dry_run: DryRun = False,
) -> None:
    """Deploy the images one environment runs to another."""
    cloud = GoogleCloud()
    _report(
        deploy.promote(
            cloud,
            name,
            deploy.ENVIRONMENTS[source],
            deploy.ENVIRONMENTS[target],
            dry_run=dry_run,
            actor=_actor(cloud),
        )
    )


def main(argv: list[str] | None = None) -> int:
    """Run the CLI, turning deploy failures into exit codes.

    Typer exits by itself on success and on usage errors (exit 2).
    """
    try:
        app(args=argv, prog_name="hackbot-deploy")
    except deploy.WaitingForInfra as exc:
        print(f"waiting for infra: {exc}", file=sys.stderr)
        return EXIT_WAITING_FOR_INFRA
    except (deploy.DeployError, manifest.ManifestError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
