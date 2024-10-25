# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import logging
import subprocess

from bugbug import repository, rust_code_analysis_server
from bugbug.code_search.function_search import (
    Function,
    FunctionSearch,
    register_function_search,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("function_search_parser")


def search(repo_dir, commit_hash, symbol_name):
    code_analysis_server = rust_code_analysis_server.RustCodeAnalysisServer()

    found_functions = []

    try:
        result = subprocess.run(
            [
                "hg",
                "grep",
                rf"{symbol_name}\(",
                "--rev",
                commit_hash,
                "--files-with-matches",
            ],
            cwd=repo_dir,
            check=True,
            capture_output=True,
        )
    except subprocess.CalledProcessError as e:
        # A return code of 1 means no functions were found (grep returns 1 when there are no results).
        if e.returncode == 1:
            return []

        logger.error(
            f"Error running 'hg grep' command.\nstdout:\n{e.stdout.decode()}\n\nstderr:\n{e.stderr.decode()}"
        )
        raise

    lines = [line for line in result.stdout.decode().split("\n") if line]

    for path_rev in lines:
        path = path_rev.split(":")[0]

        try:
            result = subprocess.run(
                ["hg", "cat", path, "--rev", commit_hash],
                cwd=repo_dir,
                check=True,
                capture_output=True,
            )
        except subprocess.CalledProcessError as e:
            logger.error(
                f"Error running 'hg cat' command.\nstdout:\n{e.stdout.decode()}\n\nstderr:\n{e.stderr.decode()}"
            )
            raise

        source = result.stdout

        metrics = code_analysis_server.metrics(path, source, unit=False)
        if "spaces" not in metrics:
            continue

        functions = repository.get_functions_from_metrics(metrics["spaces"])

        for function in functions:
            if symbol_name in function["name"]:
                found_functions.append(
                    Function(
                        function["name"],
                        function["start_line"],
                        path,
                        "\n".join(
                            source.decode().split("\n")[
                                function["start_line"] - 1 : function["end_line"]
                            ]
                        ),
                    )
                )

    code_analysis_server.terminate()

    return found_functions


def find_functions_for_lines(get_file, commit_hash, path, deleted_lines, added_lines):
    code_analysis_server = rust_code_analysis_server.RustCodeAnalysisServer()

    source = get_file(commit_hash, path)
    metrics = code_analysis_server.metrics(path, source, unit=False)
    if "spaces" not in metrics:
        return None
    functions = repository.get_touched_functions(
        metrics["spaces"], deleted_lines, added_lines
    )

    code_analysis_server.terminate()

    results = []
    for function in functions:
        results.append(
            Function(
                function["name"],
                function["start_line"],
                path,
                "\n".join(
                    source.split("\n")[
                        function["start_line"] - 1 : function["end_line"]
                    ]
                ),
            )
        )
    return results


class FunctionSearchParser(FunctionSearch):
    def __init__(self, repo_dir, get_file):
        super().__init__()
        self.repo_dir = repo_dir
        self.get_file = get_file

    def get_function_by_line(
        self, commit_hash: str, path: str, line: int
    ) -> list[Function]:
        return find_functions_for_lines(self.get_file, commit_hash, path, [], [line])

    def get_function_by_name(
        self, commit_hash: str, path: str, function_name: str
    ) -> list[Function]:
        return search(self.repo_dir, commit_hash, function_name)


register_function_search("parser", FunctionSearchParser)

if __name__ == "__main__":
    import os
    import sys

    from bugbug import utils

    def get_file(commit_hash, path):
        r = utils.get_session("hgmo").get(
            f"https://hg.mozilla.org/mozilla-unified/raw-file/{commit_hash}/{path}",
            headers={
                "User-Agent": utils.get_user_agent(),
            },
        )
        r.raise_for_status()
        return r.text

    repo_dir = sys.argv[1]

    with open(
        os.path.join(
            repo_dir,
            "browser/components/asrouter/modules/CFRPageActions.sys.mjs",
        ),
        "r",
    ) as f:
        content = f.read()

    print("RESULT 1")
    print(
        find_functions_for_lines(
            get_file,
            "4ba1b499b812",
            "browser/components/asrouter/modules/CFRPageActions.jsm",
            [],
            [356],
        )
    )

    print("RESULT 2")
    print(search(repo_dir, "4ba1b499b812", "getStrings"))

    print("RESULT 3")
    print(search(repo_dir, "4ba1b499b812", "itdoesntexistofcourse"))
