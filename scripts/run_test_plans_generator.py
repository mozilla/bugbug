# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

"""Run the test plans generator tool locally."""

import argparse
import json

from bugbug.tools.test_plans_generator.agent import TestPlanGenerationTool


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--feature-description",
        required=True,
        help="Description of the feature to generate test cases for.",
    )
    parser.add_argument(
        "--test-scope",
        required=True,
        help="Scope of testing for the feature.",
    )
    parser.add_argument(
        "--qa-test-cases",
        default="",
        help="Existing QA test cases to avoid duplicating.",
    )
    parser.add_argument(
        "--no-test-steps",
        dest="generate_steps",
        action="store_false",
        help="Only generate test cases, without detailed test steps.",
    )
    parser.add_argument(
        "--json-lines",
        action="store_true",
        help="Print one JSON object per generation phase.",
    )

    return parser.parse_args()


def _load_json(output: str, output_name: str) -> dict:
    try:
        return json.loads(output)
    except json.JSONDecodeError as e:
        raise SystemExit(
            f"The model did not return valid {output_name} JSON: {e}"
        ) from e


def _print_json(data: dict, json_lines: bool = False) -> None:
    if json_lines:
        print(json.dumps(data), flush=True)
        return

    print(json.dumps(data, indent=2))


def main() -> None:
    args = parse_args()

    tool = TestPlanGenerationTool.create()

    generated_test_cases = tool.generate_test_cases(
        feature_description=args.feature_description,
        test_scope=args.test_scope,
        qa_test_cases=args.qa_test_cases,
    )
    test_cases = _load_json(generated_test_cases, "test cases")

    if not args.generate_steps:
        if args.json_lines:
            _print_json({"type": "test_cases", **test_cases}, json_lines=True)
        else:
            _print_json(test_cases)
        return

    if args.json_lines:
        _print_json({"type": "test_cases", **test_cases}, json_lines=True)

    generated_test_steps = tool.generate_test_steps(
        feature_description=args.feature_description,
        test_cases=generated_test_cases,
    )
    test_plan = _load_json(generated_test_steps, "test steps")

    if args.json_lines:
        _print_json({"type": "test_steps", **test_plan}, json_lines=True)
        return

    _print_json(test_plan)


if __name__ == "__main__":
    main()
