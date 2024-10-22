# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import io
import json
from typing import Iterable, Literal

from lxml.html import HtmlElement
from requests_html import HTMLSession

from bugbug import utils
from bugbug.code_search import searchfox_data
from bugbug.code_search.function_search import (
    Function,
    FunctionSearch,
    register_function_search,
)


def get_line_number(elements: Iterable[HtmlElement], position: Literal["start", "end"]):
    if position == "start":
        element = next(iter(elements))
    else:
        *_, element = iter(elements)

    if "data-nesting-sym" in element.attrib:
        return get_line_number(element.iterdescendants(), position)

    return int(element.get("id")[len("line-") :])


# TODO: we should use commit_hash...
def get_functions(commit_hash, path, symbol_name=None):
    html_session = HTMLSession()

    r = html_session.get(
        f"https://searchfox.org/mozilla-central/source/{path}",
        headers={
            "User-Agent": utils.get_user_agent(),
        },
    )
    r.raise_for_status()

    # TODO: this simplification depends on https://github.com/scrapy/cssselect/issues/139.
    # sym_wraps = r.html.find(f"[data-nesting-sym*='{symbol_name}' i]")
    sym_wraps = []
    file = r.html.find("#file")
    assert len(file) == 1
    for element in file[0].element.iterdescendants():
        if "data-nesting-sym" in element.attrib and (
            symbol_name is None or symbol_name in element.attrib["data-nesting-sym"]
        ):
            sym_wraps.append(element)

    functions = []

    for sym_wrap in sym_wraps:
        functions.append(
            {
                "name": sym_wrap.attrib["data-nesting-sym"][1:],
                "path": path,
                "start": get_line_number(sym_wrap, "start"),
                "end": get_line_number(sym_wrap, "end"),
            }
        )

    return functions


def find_function_for_line(commit_hash, path, line):
    functions = get_functions(commit_hash, path, symbol_name=None)

    selected_function = None

    for function in functions:
        if function["start"] <= line <= function["end"]:
            if (
                selected_function is None
                or selected_function["start"] < function["start"]
            ):
                # We want to return the closest scope. For example, for line https://searchfox.org/mozilla-central/rev/6b8a3f804789fb865f42af54e9d2fef9dd3ec74d/browser/components/asrouter/modules/CFRPageActions.jsm#333,
                # we have:
                # {'path': 'browser/components/asrouter/modules/CFRPageActions.jsm', 'start': 65, 'end': 878}
                # {'path': 'browser/components/asrouter/modules/CFRPageActions.jsm', 'start': 326, 'end': 362}
                selected_function = function

    return selected_function


# TODO: we should use commit_hash...
def search(commit_hash, symbol_name):
    r = utils.get_session("searchfox").get(
        f"https://searchfox.org/mozilla-central/search?q={symbol_name}",
        headers={
            "User-Agent": utils.get_user_agent(),
        },
    )
    r.raise_for_status()

    results = r.text
    results = results.split("var results = ", 1)[1]
    results = results.split(";\n", 1)[0]

    # A workaround to fix: https://github.com/mozilla/bugbug/issues/4448
    results = results.replace(r"<\s", r"<\\s")

    results = json.loads(results)

    definitions = []
    for type_ in ["normal", "thirdparty", "test"]:
        if type_ not in results:
            continue

        for sub_type, value in results[type_].items():
            if sub_type.startswith("Definitions") and sub_type.endswith(
                f"{symbol_name})"
            ):
                definitions.extend(value)

    paths = list(set(definition["path"] for definition in definitions))

    return sum((get_functions(commit_hash, path, symbol_name) for path in paths), [])


class FunctionSearchSearchfoxAPI(FunctionSearch):
    def __init__(self, get_file):
        super().__init__()
        self.get_file = get_file

    def definitions_to_results(self, commit_hash, definitions):
        result = []

        for definition in definitions:
            source = searchfox_data.extract_source(
                definition["path"],
                definition["start"],
                definition["end"] + 1
                if definition["end"] != definition["start"]
                else definition["end"],
                read_mc_path=lambda path: io.StringIO(
                    self.get_file(
                        commit_hash or "tip",
                        path,
                    )
                ),
            )
            result.append(
                Function(
                    definition["name"],
                    definition["start"],
                    definition["path"],
                    "\n".join(source),
                )
            )

        return result

    def get_function_by_line(
        self, commit_hash: str, path: str, line: int
    ) -> list[Function]:
        definition = find_function_for_line(
            commit_hash or "tip",
            path,
            line,
        )
        return (
            self.definitions_to_results(commit_hash, [definition])
            if definition is not None
            else []
        )

    def get_function_by_name(
        self, commit_hash: str, path: str, function_name: str
    ) -> list[Function]:
        definitions = search(
            commit_hash or "tip",
            function_name,
        )

        return self.definitions_to_results(commit_hash, definitions)


register_function_search("searchfox_api", FunctionSearchSearchfoxAPI)


if __name__ == "__main__":
    print("RESULT1")
    print(search("hash", "getStrings"))

    import io

    def get_file(commit_hash, path):
        r = utils.get_session("hgmo").get(
            f"https://hg.mozilla.org/mozilla-unified/raw-file/{commit_hash}/{path}"
        )
        r.raise_for_status()
        return r.text

    definitions = search("hash", "GetFramebufferForBuffer")
    print("RESULT2")
    print(definitions)
    result = []
    for definition in definitions:
        source = searchfox_data.extract_source(
            definition["path"],
            definition["start"],
            definition["end"] + 1
            if definition["end"] != definition["start"]
            else definition["end"],
            read_mc_path=lambda path: io.StringIO(get_file("tip", path)),
        )
        result.append(
            {
                "start": definition["start"],
                "file": definition["path"],
                "source": source,
                "annotations": [],
            }
        )
    print("RESULT3")
    print(result)

    func = find_function_for_line(
        "hash", "browser/components/asrouter/modules/CFRPageActions.sys.mjs", 333
    )
    print("RESULT4")
    print(
        searchfox_data.extract_source(
            func["path"],
            func["start"],
            func["end"] + 1 if func["end"] != func["start"] else func["end"],
            read_mc_path=lambda path: io.StringIO(
                get_file(
                    "tip", "browser/components/asrouter/modules/CFRPageActions.sys.mjs"
                )
            ),
        )
    )
