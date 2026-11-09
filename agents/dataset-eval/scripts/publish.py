# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

"""Publish an eval dataset and its prompts from a local directory to Weave.

The directory holds only data, kept off the repo:

    <dir>/dataset/<id>.json   one row each: {"prompt", "tests"}
    <dir>/prompts/<name>.md   e.g. judge-rubric.md, published as <dataset>-<name>

Usage:
    uv run --package hackbot-agent-dataset-eval python agents/dataset-eval/scripts/publish.py \
        --dir ~/private/evals --name my-dataset [--project ENTITY/PROJECT] [--dry-run]
"""

import argparse
from pathlib import Path

from agent_tools.firefox.tools.run_tests import TEST_ROOTS
from hackbot_agents.dataset_eval import dataset


def check(row: dict) -> None:
    if not row.get("prompt"):
        raise SystemExit(f"{row['id']}: no prompt")
    for suite in row.get("tests", []):
        if suite.get("harness") not in TEST_ROOTS or not suite.get("paths"):
            raise SystemExit(
                f"{row['id']}: a test suite needs a known harness and paths"
            )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dir", type=Path, required=True)
    parser.add_argument("--name", required=True, help="dataset name in Weave")
    parser.add_argument("--project", default="moz-bugbug/hackbot-evals")
    parser.add_argument("--dry-run", action="store_true", help="validate only")
    args = parser.parse_args()

    root = args.dir.expanduser()
    rows = dataset.load(str(root / "dataset"))
    for row in rows:
        check(row)
    prompts = {p.stem: p.read_text() for p in sorted((root / "prompts").glob("*.md"))}
    print(f"{len(rows)} rows; prompts: {', '.join(prompts) or 'none'}")
    if args.dry_run:
        return

    import weave

    weave.init(args.project)
    print(
        "dataset",
        weave.publish(weave.Dataset(name=args.name, rows=rows), name=args.name).uri(),
    )
    for name, text in prompts.items():
        print(
            name,
            weave.publish(weave.StringPrompt(text), name=f"{args.name}-{name}").uri(),
        )


if __name__ == "__main__":
    main()
