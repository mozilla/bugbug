# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

"""Evaluate how the review agent uses review-context.toml guidelines.

Runs the production review pipeline (CodeReviewTool.run) on Phabricator
revisions and records, for each review, the full transcript, the documents the
agent read, its tool calls, its comments, and whether it caught the issues a
human reviewer found (see scripts/code_review_context_eval_cases.toml).

Phabricator is only read. Reviews run concurrently, like on reviewhelper-api,
whose reviews share one event loop; --parallelism bounds how many at once.

Environment: ANTHROPIC_API_KEY, PHABRICATOR_URL, PHABRICATOR_API_KEY and
GITHUB_TOKEN (unauthenticated GitHub API requests are quickly rate limited).
--with-examples and --with-filter use the production vector databases, which
also need QDRANT_LOCATION, QDRANT_API_KEY and OPENAI_API_KEY.

To evaluate guideline changes before landing them, point --checkout to a local
Firefox checkout: its review-context.toml rules are merged into the landed ones,
and the files matching each --override glob replace their landed version, both
when rules load them and when the agent reads them.

Usage:
    python scripts/code_review_context_eval.py run runs/baseline --trials 2
    python scripts/code_review_context_eval.py run runs/new --case D337832 \
        --checkout ~/firefox --override '.claude/skills/desktop-theme*/**/*.md'
    python scripts/code_review_context_eval.py summarize runs/baseline runs/new
"""

import argparse
import asyncio
import dataclasses
import json
import tomllib
from pathlib import Path

from langchain.chat_models import init_chat_model

from bugbug.tools.code_review.agent import DEFAULT_REVIEW_LLM_KWARGS, CodeReviewTool
from bugbug.tools.core.llms import DEFAULT_ANTHROPIC_MODEL
from bugbug.tools.core.platforms.phabricator import PhabricatorPatch

DEFAULT_CASES = Path(__file__).with_name("code_review_context_eval_cases.toml")
# How far from the expected line a comment can be anchored and still count.
LINE_TOLERANCE = 3


class TranscriptRecordingTool(CodeReviewTool):
    async def _invoke_review_agent(self, context, messages):
        result = await super()._invoke_review_agent(context, messages)
        self.transcript = result["messages"] if result else messages
        return result


class NoFilter:
    def run(self, suggestions):
        return suggestions


def case_name(case: dict) -> str:
    return f"D{case['revision']}"


def is_document_read(call: dict) -> bool:
    if call["name"] == "read_review_guideline":
        return True
    path = str(call["args"].get("file_path") or "")
    return call["name"] == "expand_context" and path.endswith((".md", ".rst"))


def caught(expected: dict, comments: list[dict]) -> bool:
    return any(
        comment["filename"] == expected["file"]
        and abs(comment["start_line"] - expected["line"]) <= LINE_TOLERANCE
        for comment in comments
    )


def dump_messages(messages) -> list[dict]:
    dumped = []
    for message in messages:
        entry = {"type": message.type, "content": message.content}
        if getattr(message, "tool_calls", None):
            entry["tool_calls"] = [
                {"name": call["name"], "args": call["args"]}
                for call in message.tool_calls
            ]
        if message.type == "tool":
            entry["name"] = message.name
        dumped.append(entry)
    return dumped


async def review(case: dict, trial: int, out_dir: Path, args) -> None:
    llm = init_chat_model(
        DEFAULT_ANTHROPIC_MODEL,
        **{
            **DEFAULT_REVIEW_LLM_KWARGS,
            # Only changes what the API returns, not how the model behaves.
            "thinking": {"type": "adaptive", "display": "summarized"},
        },
    )
    kwargs = {"llm": llm}
    if args.checkout:
        kwargs["extra_context_toml"] = (
            args.checkout / "review-context.toml"
        ).read_text()
        kwargs["content_overrides"] = {
            str(path.relative_to(args.checkout)): path.read_text()
            for pattern in args.override
            for path in args.checkout.glob(pattern)
            if path.is_file()
        }
    if not args.with_examples:
        kwargs["review_comments_db"] = None
    if not args.with_filter:
        kwargs["suggestion_filterer"] = NoFilter()
    tool = TranscriptRecordingTool.create(**kwargs)

    patch = PhabricatorPatch(revision_id=case["revision"], diff_id=case.get("diff"))
    record = {"case": case, "trial": trial}
    try:
        result = await tool.run(patch)
    except Exception as e:
        record["error"] = repr(e)
    else:
        comments = [dataclasses.asdict(c) for c in result.review_comments]
        tool_calls = result.details["tool_calls"]
        record |= {
            "general_comment": result.general_comment,
            "comments": comments,
            "tool_calls": tool_calls,
            "documents_read": sorted(
                {c["args"]["file_path"] for c in tool_calls if is_document_read(c)}
            ),
            "caught": [caught(e, comments) for e in case.get("expected", [])],
            "external_content": [
                item["name"] for item in result.details["external_content"]
            ],
            "external_content_bytes": sum(
                item["bytes"] for item in result.details["external_content"]
            ),
            "tool_output_chars": sum(
                len(str(m.content))
                for m in getattr(tool, "transcript", [])
                if m.type == "tool"
            ),
        }
    record["messages"] = dump_messages(getattr(tool, "transcript", []))
    path = out_dir / f"{case_name(case)}-{trial}.json"
    path.write_text(json.dumps(record, indent=1, default=str))
    print(f"{path.name}: {summarize_record(record)}", flush=True)


def summarize_record(record: dict) -> str:
    if "error" in record:
        return f"ERROR {record['error']}"
    caught_ = record["caught"]
    return (
        f"{len(record['documents_read'])} documents read, "
        f"{len(record['tool_calls'])} tool calls, "
        f"{len(record['comments'])} comments"
        + (f", caught {sum(caught_)}/{len(caught_)}" if caught_ else "")
    )


async def run(args) -> None:
    cases = tomllib.loads(args.cases.read_text())["cases"]
    if args.case:
        cases = [case for case in cases if case_name(case) in args.case]
    args.out_dir.mkdir(parents=True, exist_ok=True)
    semaphore = asyncio.Semaphore(args.parallelism)

    async def bounded_review(case, trial):
        async with semaphore:
            await review(case, trial, args.out_dir, args)

    await asyncio.gather(
        *(bounded_review(case, trial) for trial in range(args.trials) for case in cases)
    )
    summarize([args.out_dir])


def summarize(run_dirs: list[Path]) -> None:
    for run_dir in run_dirs:
        records = [json.loads(p.read_text()) for p in sorted(run_dir.glob("D*.json"))]
        done = [r for r in records if "error" not in r]
        if not done:
            print(f"{run_dir}: no successful reviews out of {len(records)}")
            continue
        expected = [c for r in done for c in r["caught"]]
        print(
            f"{run_dir}: {len(done)}/{len(records)} reviews, "
            f"{sum(bool(r['documents_read']) for r in done)} with document reads, "
            f"{sum(len(r['documents_read']) for r in done) / len(done):.1f} "
            "documents/review, "
            f"{sum(len(r['tool_calls']) for r in done) / len(done):.0f} tool calls/review, "
            f"{sum(r['external_content_bytes'] for r in done) / len(done) / 1000:.0f}k "
            "preloaded bytes/review, "
            f"{sum(r['tool_output_chars'] for r in done) / len(done) / 1000:.0f}k "
            "tool output chars/review, "
            f"{sum(len(r['comments']) for r in done) / len(done):.1f} comments/review"
            + (f", caught {sum(expected)}/{len(expected)}" if expected else "")
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="Review the cases.")
    run_parser.add_argument("out_dir", type=Path)
    run_parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    run_parser.add_argument(
        "--case", action="append", help="Only review this case, e.g. D337832."
    )
    run_parser.add_argument("--trials", type=int, default=1)
    run_parser.add_argument(
        "--parallelism",
        type=int,
        default=8,
        help="Maximum concurrent reviews, to stay under the API rate limits.",
    )
    run_parser.add_argument(
        "--checkout",
        type=Path,
        help="Local Firefox checkout whose review-context.toml rules to use.",
    )
    run_parser.add_argument(
        "--override",
        action="append",
        default=[],
        help="Glob, relative to --checkout, of files to use instead of the landed ones.",
    )
    run_parser.add_argument(
        "--with-examples",
        action="store_true",
        help="Include similar past comments as examples, as in production.",
    )
    run_parser.add_argument(
        "--with-filter",
        action="store_true",
        help="Filter the comments, as in production.",
    )

    summarize_parser = subparsers.add_parser(
        "summarize", help="Compare the reviews of earlier runs."
    )
    summarize_parser.add_argument("run_dirs", type=Path, nargs="+")

    args = parser.parse_args()
    if args.command == "run":
        asyncio.run(run(args))
    else:
        summarize(args.run_dirs)


if __name__ == "__main__":
    main()
