"""Command line for hackbot agent deploys.

Exit codes: 0 done (or nothing to do), 1 failed, 3 waiting for infra. A build
pipeline should treat 3 as "skip the remaining environments", not as a failure.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from hackbot_deploy import deploy, manifest

EXIT_WAITING_FOR_INFRA = 3


def _actor() -> str:
    for var in ("HACKBOT_DEPLOY_ACTOR", "GITHUB_ACTOR", "BUILD_ID"):
        if os.environ.get(var):
            return f"{var.lower()}:{os.environ[var]}"
    return os.environ.get("USER", "unknown")


def _cmd_manifest(args: argparse.Namespace) -> int:
    previous = json.loads(args.previous.read_text()) if args.previous else None
    result, changes = manifest.build_manifest(
        manifest.load_configs(args.agents_dir), previous
    )
    text = manifest.dumps(result)
    for name, change in sorted(changes.items()):
        print(f"{change:>9}  {name}", file=sys.stderr)
    if args.check:
        if args.check.read_text() != text:
            print(f"{args.check} is out of date", file=sys.stderr)
            return 1
        return 0
    if args.output in (None, "-"):
        sys.stdout.write(text)
    else:
        Path(args.output).write_text(text)
    return 0


def _cmd_info(args: argparse.Namespace) -> int:
    config = manifest.load_config(args.agents_dir / args.agent / "hackbot.toml")
    if config is None:
        print(f"{args.agent} has no [deploy] table", file=sys.stderr)
        return 1
    entry = config.manifest_entry()
    # Shell-sourceable, for build steps.
    print(f"CONFIG_HASH={manifest.config_hash(args.agent, entry)}")
    print(f"RUNTIME={entry['runtime']}")
    print(f"HAS_BROKER={'true' if 'broker' in entry else 'false'}")
    return 0


def _images(args: argparse.Namespace) -> dict[str, str] | None:
    if not args.agent_image:
        return None
    images = {"agent": args.agent_image}
    if args.broker_image:
        images["broker"] = args.broker_image
    return images


def _run_deploy(args: argparse.Namespace) -> int:
    cloud = deploy.GoogleCloud()
    common = {
        "force": getattr(args, "force", False),
        "dry_run": args.dry_run,
        "actor": _actor(),
    }
    if args.command == "promote":
        result = deploy.promote_agent(
            cloud,
            args.agent,
            deploy.ENVIRONMENTS[args.source],
            deploy.ENVIRONMENTS[args.target],
            **common,
        )
    else:
        result = deploy.deploy_agent(
            cloud,
            args.agent,
            deploy.ENVIRONMENTS[args.env],
            _images(args),
            rollback=args.command == "rollback",
            **common,
        )
    print(
        f"{result.agent} in {result.environment}: {result.action} ({result.config_hash})"
    )
    for container, image in sorted(result.images.items()):
        print(f"  {container}: {image}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="hackbot-deploy", description=__doc__)
    parser.add_argument(
        "--agents-dir", type=Path, default=Path("agents"), help="default: ./agents"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser(
        "manifest", help="generate agents.json from every agent's [deploy] table"
    )
    p.add_argument(
        "--previous", type=Path, help="the agents.json currently in webservices-infra"
    )
    p.add_argument("--output", help="where to write it (default: stdout)")
    p.add_argument(
        "--check",
        type=Path,
        help="exit 1 if this file differs from what would be generated",
    )
    p.set_defaults(func=_cmd_manifest)

    p = sub.add_parser(
        "info", help="print an agent's config hash and shape, shell-sourceable"
    )
    p.add_argument("agent")
    p.set_defaults(func=_cmd_info)

    envs = sorted(deploy.ENVIRONMENTS)
    for name, help_text in (
        ("deploy", "deploy images to an environment"),
        ("rollback", "roll back to an earlier deployment"),
    ):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("agent")
        p.add_argument("--env", required=True, choices=envs)
        p.add_argument("--agent-image", required=name == "deploy", help="…@sha256:…")
        p.add_argument("--broker-image", help="…@sha256:…, for agents with a broker")
        if name == "deploy":
            p.add_argument(
                "--force",
                action="store_true",
                help="deploy even if a newer build is already running",
            )
        p.add_argument("--dry-run", action="store_true")
        p.set_defaults(func=_run_deploy)

    p = sub.add_parser(
        "promote", help="deploy the images one environment runs to another"
    )
    p.add_argument("agent")
    p.add_argument("--from", dest="source", required=True, choices=envs)
    p.add_argument("--to", dest="target", required=True, choices=envs)
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=_run_deploy)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except deploy.WaitingForInfra as exc:
        print(f"waiting for infra: {exc}", file=sys.stderr)
        return EXIT_WAITING_FOR_INFRA
    except (deploy.DeployError, manifest.ManifestError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
