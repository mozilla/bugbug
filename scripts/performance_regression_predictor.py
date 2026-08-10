# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

"""Run the Performance Regression Predictor against a local patch."""

from __future__ import annotations

import argparse
import json
import re
import sys
from email import policy
from email.parser import Parser
from pathlib import Path

from bugbug.models.performance_regression_predictor import (
    PerformanceRegressionPredictorModel,
)


def extract_commit_message_from_patch(patch: str) -> str | None:
    """Extract a message from Git format-patch or Mercurial export content."""
    if patch.startswith("# HG changeset patch"):
        message_lines: list[str] = []
        metadata_finished = False
        for line in patch.splitlines()[1:]:
            if not metadata_finished and (line.startswith("#") or not line.strip()):
                continue
            metadata_finished = True
            if line.startswith(("diff -r ", "diff --git ")):
                break
            message_lines.append(line)
        message = "\n".join(message_lines).strip()
        return message or None

    if re.search(r"^Subject:", patch, flags=re.MULTILINE):
        email_message = Parser(policy=policy.default).parsestr(patch)
        subject = str(email_message.get("Subject", "")).strip()
        body = email_message.get_payload()
        if not isinstance(body, str):
            body = ""
        body = re.split(r"^---\s*$|^diff --git ", body, maxsplit=1, flags=re.MULTILINE)[
            0
        ].strip()
        message = "\n\n".join(part for part in (subject, body) if part)
        return message or None

    return None


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Predict performance-regression risk from a local patch",
    )
    parser.add_argument(
        "--model-dir",
        required=True,
        help="Hugging Face checkpoint directory",
    )
    parser.add_argument("--patch-file", required=True, type=Path)
    commit_message = parser.add_mutually_exclusive_group()
    commit_message.add_argument("--commit-message")
    commit_message.add_argument("--commit-message-file", type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    raw_diff = args.patch_file.read_text(encoding="utf-8")

    if args.commit_message is not None:
        commit_message = args.commit_message
        commit_message_source = "argument"
    elif args.commit_message_file is not None:
        commit_message = args.commit_message_file.read_text(encoding="utf-8")
        commit_message_source = "file"
    else:
        commit_message = extract_commit_message_from_patch(raw_diff) or ""
        commit_message_source = "patch" if commit_message else "none"
        if not commit_message:
            print(
                "Warning: no commit message was found; predicting from the diff only",
                file=sys.stderr,
            )

    model = PerformanceRegressionPredictorModel.load(args.model_dir)
    probabilities = model.classify(
        [{"commit_message": commit_message, "diff": raw_diff}],
        probabilities=True,
    )[0]
    predicted_class = int(probabilities.argmax())
    result = {
        "prob": probabilities.tolist(),
        "class": predicted_class,
        "risk_score": float(probabilities[1]),
        "extra_data": {
            **model.get_extra_data(),
            "commit_message_source": commit_message_source,
        },
    }
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
