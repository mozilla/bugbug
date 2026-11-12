"""Releases: what a build releases, and deploying what it built.

A release is a version bump. A build plans which deployables to release into
its environment (``plan``), builds their images, then deploys them one after
another (``run``); one failing doesn't stop the rest.

The plan is a text file the build config reads too, one deployable per line:
``name version previous-commit config-hash broker``, "-" for none.
"""

import re
from dataclasses import dataclass
from pathlib import Path

from hackbot_deploy import deploy, manifest

# dev/agents/<agent> or dev/services/<service>, with an optional /<anything>.
_DEV_BRANCH = re.compile(
    r"dev/(?P<kind>agents|services)/(?P<name>[a-z0-9][a-z0-9-]*)(/.+)?"
)


@dataclass(frozen=True)
class Planned:
    name: str
    version: str
    previous: str | None = None
    config_hash: str | None = None
    has_broker: bool = False

    def line(self) -> str:
        fields = [self.name, self.version, self.previous, self.config_hash]
        return " ".join(f or "-" for f in fields) + (
            " broker" if self.has_broker else " -"
        )

    @classmethod
    def parse(cls, line: str) -> "Planned":
        name, version, previous, config_hash, broker = line.split()
        return cls(
            name,
            version,
            None if previous == "-" else previous,
            None if config_hash == "-" else config_hash,
            broker == "broker",
        )


def from_branch(branch: str) -> str:
    """The deployable a dev branch names: dev/agents/bug-fix/x -> agents/bug-fix."""
    match = _DEV_BRANCH.fullmatch(branch)
    if match is None:
        raise deploy.DeployError(
            f"{branch}: expected dev/agents/<agent> or dev/services/<service>, optionally /<anything>"
        )
    return f"{match['kind']}/{match['name']}"


def by_hand(pusher: str) -> bool:
    """Whether a build was started by hand: no pusher, or its binding unresolved."""
    return not pusher or pusher.startswith("$(")


def plan(
    cloud: deploy.Cloud,
    env: deploy.Environment,
    root: Path,
    *,
    names: list[str] | None = None,
    branch: str = "",
    pusher: str = "",
) -> list[Planned]:
    """Decide what to release into ``env``.

    dev releases what the branch names, always. Elsewhere, a push releases
    what changed version among ``names`` (default: every agent with a
    [deploy] table); a build started by hand releases them regardless.
    """
    if env.name == "dev":
        candidates, force = [from_branch(branch)], True
    else:
        candidates = names or [
            f"{deploy.AGENTS}/{agent}"
            for agent in sorted(manifest.load_configs(root / deploy.AGENTS))
        ]
        force = by_hand(pusher)

    planned = []
    for name in candidates:
        kind, short = deploy.split_id(name)
        version = manifest.package_version(root, name)
        needed, previous = deploy.release_needed(cloud, env, name, version)
        if not (needed or force):
            continue
        config_hash, has_broker = None, False
        if kind == deploy.AGENTS:
            config = manifest.load_config(root / name / "hackbot.toml")
            if config is None:
                raise deploy.DeployError(f"{name} has no [deploy] table")
            entry = config.manifest_entry()
            config_hash, has_broker = (
                manifest.config_hash(short, entry),
                "broker" in entry,
            )
        planned.append(Planned(name, version, previous, config_hash, has_broker))
    return planned


def built_images(planned: Planned, digests: Path) -> dict[str, str] | None:
    """The digests the build wrote for ``planned``, or None if it didn't build.

    The build writes <agent>-agent.digest and <agent>-broker.digest for an
    agent, <service>.digest for a service.
    """
    kind, short = deploy.split_id(planned.name)
    if kind == deploy.SERVICES:
        files = {short: digests / f"{short}.digest"}
    else:
        containers = ["agent", "broker"] if planned.has_broker else ["agent"]
        files = {c: digests / f"{short}-{c}.digest" for c in containers}
    if not all(path.exists() for path in files.values()):
        return None
    return {container: path.read_text().strip() for container, path in files.items()}


def run(
    cloud: deploy.Cloud,
    env: deploy.Environment,
    planned: list[Planned],
    digests: Path,
    *,
    actor: str,
) -> tuple[list[str], list[str]]:
    """Deploy every planned deployable that built. Return what was released and what failed."""
    released, failed = [], []
    for item in planned:
        kind, short = deploy.split_id(item.name)
        images = built_images(item, digests)
        if images is None:
            failed.append(f"{item.name}: its images didn't build")
            continue
        common = {"force": env.name == "dev", "actor": actor}
        try:
            if kind == deploy.SERVICES:
                deploy.deploy_service(cloud, short, env, images[short], **common)
            else:
                deploy.deploy_agent(cloud, short, env, images, **common)
        except deploy.WaitingForInfra:
            failed.append(
                f"{item.name}: its shape isn't applied in {env.name}; apply the infra PR, then rerun"
            )
        except deploy.DeployError as exc:
            failed.append(f"{item.name}: {exc}")
        else:
            released.append(f"{item.name} {item.version}")
    return released, failed
