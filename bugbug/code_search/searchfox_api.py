# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import json

import requests
import utils
from requests_html import HTMLSession


# TODO: we should use commit_hash...
def get_functions(commit_hash, path, symbol_name=None):
    html_session = HTMLSession()

    r = html_session.get(f"https://searchfox.org/mozilla-central/source/{path}")
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
        start_line_elem_id = sym_wrap.getchildren()[0].get("id")
        start_line = int(start_line_elem_id[len("line-") :])

        end_line_elem_id = sym_wrap.getchildren()[-1].get("id")
        end_line = int(end_line_elem_id[len("line-") :])

        functions.append(
            {
                "name": sym_wrap.attrib["data-nesting-sym"][1:],
                "path": path,
                "start": start_line,
                "end": end_line,
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
    r = requests.get(f"https://searchfox.org/mozilla-central/search?q={symbol_name}")
    r.raise_for_status()
    after_results = r.text[r.text.index("var results = ") + len("var results = ") :]
    results = json.loads(after_results[: after_results.index(";\n")])

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


if __name__ == "__main__":
    print(search("hash", "getStrings"))

    import io

    import searchfox_search

    definitions = search("hash", "GetFramebufferForBuffer")
    print(definitions)
    result = []
    for definition in definitions:
        source = searchfox_search.extract_source(
            definition["path"],
            definition["start"],
            definition["end"] + 1
            if definition["end"] != definition["start"]
            else definition["end"],
            read_mc_path=lambda path: io.StringIO(utils.get_file("tip", path)),
        )
        result.append(
            {
                "start": definition["start"],
                "file": definition["path"],
                "source": source,
                "annotations": [],
            }
        )
    print(result)

    func = find_function_for_line(
        "hash", "browser/components/asrouter/modules/CFRPageActions.jsm", 333
    )
    print(
        searchfox_search.extract_source(
            func["path"],
            func["start"],
            func["end"] + 1 if func["end"] != func["start"] else func["end"],
            read_mc_path=lambda path: io.StringIO(
                utils.get_file(
                    "tip", "browser/components/asrouter/modules/CFRPageActions.jsm"
                )
            ),
        )
    )
