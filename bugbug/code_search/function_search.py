# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import io

import function_search_parser
import searchfox_api
import searchfox_download
import searchfox_search
from utils import find_base_commit_hash, get_file

CPP_EXTENSIONS = [
    ".c",
    ".cpp",
    ".cc",
    ".cxx",
    ".h",
    ".hh",
    ".hpp",
    ".hxx",
    ".mm",
    ".m",
]


def get_function(diff, patch, path, function_name=None, line=None, fast=False):
    base_commit_hash = find_base_commit_hash(diff)

    # In fast mode, only use Searchfox website.
    # In slow mode (for historical data), try Searchfox first and fallback on parsing with rust-code-analysis.

    result = []

    if not fast:
        try:
            searchfox_path = searchfox_download.fetch(base_commit_hash)
        except searchfox_download.SearchfoxDataNotAvailable:
            searchfox_path = None

        if searchfox_path is not None and any(
            path.endswith(ext) for ext in CPP_EXTENSIONS
        ):
            if function_name is not None:
                # TODO: Try looking for a function call within the "before patch" first (it'll be more precise, as we can identify the exact call and so full symbol name)
                # caller_obj = {
                #     "file": path,
                #     "start": XXX,
                #     "source": XXX,  # the content doesn't matter, extract_function_approx only uses it for the number of lines
                # }
                # out = searchfox_search.extract_function_approx(
                #     function_name,
                #     caller_obj,
                #     "",
                #     searchfox_path
                # )
                # if out is not None:
                #     result.append(out)

                # If it wasn't found, try with entire file next
                if not result:
                    if base_commit_hash is None:
                        mc_file = get_file("tip", path)
                    else:
                        mc_file = get_file(base_commit_hash, path)

                    caller_obj = {
                        "file": path,
                        "start": 0,
                        "source": mc_file,  # the content doesn't matter, extract_function_approx only uses it for the number of lines
                    }

                    out = searchfox_search.extract_function_approx(
                        function_name,
                        caller_obj,
                        "",
                        searchfox_path,
                        read_mc_path=lambda path: io.StringIO(
                            get_file(
                                base_commit_hash,
                                base_commit_hash or "tip",
                                path,
                            )
                        ),
                    )
                    if out is not None:
                        result.append(out)

            # If it wasn't found, try with string matching
            if not result:
                if function_name is not None:
                    definitions = searchfox_search.find_symbol_definition(
                        searchfox_path,
                        [function_name],
                        target_sym_is_pretty=True,
                        headers_first=False,
                        target_sym_type_restriction="function",
                    )[function_name]
                elif line is not None:
                    definitions = [
                        searchfox_search.find_symbol_definition_for_line(
                            path, line, searchfox_path
                        )
                    ]

                for definition in definitions:
                    definition_path = definition["file"].replace(searchfox_path, "")
                    source = searchfox_search.extract_source(
                        definition_path,
                        definition["target_line"],
                        definition["target_end_line"],
                        read_mc_path=lambda path: io.StringIO(
                            get_file(
                                base_commit_hash,
                                base_commit_hash or "tip",
                                path,
                            )
                        ),
                    )
                    result.append(
                        {
                            "name": definition["name"],
                            "start": definition["target_line"],
                            "file": definition_path,
                            "source": source,
                            "annotations": [],
                        }
                    )

        # If we were not able to find the function by using Searchfox, fallback on parsing.
        if not result:
            if function_name is not None:
                result = function_search_parser.search(base_commit_hash, function_name)
            elif line is not None:
                result = function_search_parser.find_functions_for_lines(
                    base_commit_hash, path, [], [line]
                )

    if not result:
        if function_name is not None:
            definitions = searchfox_api.search(
                base_commit_hash or "tip",
                function_name,
            )
        elif line is not None:
            definition = searchfox_api.find_function_for_line(
                base_commit_hash or "tip",
                path,
                line,
            )
            if definition is not None:
                definitions = [definition]
            else:
                definitions = []

        for definition in definitions:
            source = searchfox_search.extract_source(
                definition["path"],
                definition["start"],
                definition["end"] + 1
                if definition["end"] != definition["start"]
                else definition["end"],
                read_mc_path=lambda path: io.StringIO(
                    get_file(
                        base_commit_hash or "tip",
                        path,
                    )
                ),
            )
            result.append(
                {
                    "name": definition["name"],
                    "start": definition["start"],
                    "file": definition["path"],
                    "source": source,
                    "annotations": [],
                }
            )

    # Filter out results when there are too many.
    # TODO: If the function is in a JS file and we found its definition in both JS and C++ files, use the definition in JS file.
    # TODO: If the function is in a non-test file and we found its definition in both test and non-test files, use the definition in the non-test file (and the opposite for functions in test files)

    found_perfect = None
    for func in result:
        if func["name"] == function_name:
            assert found_perfect is None, "Found two functions with the same name"
            found_perfect = func

    if found_perfect is not None:
        return [found_perfect]
    else:
        return result


if __name__ == "__main__":
    import config
    from libmozdata.phabricator import PhabricatorAPI

    phabricator = PhabricatorAPI(
        config.PHABRICATOR_API_KEY, "https://phabricator.services.mozilla.com/api/"
    )

    # https://phabricator.services.mozilla.com/D199272?id=811858
    diff1 = {
        "id": 811858,
        "type": "DIFF",
        "phid": "PHID-DIFF-gjfsxs2etv33x3wz3r3o",
        "attachments": {},
        "revisionPHID": "PHID-DREV-opahuaoyfaecxmzoaw7w",
        "authorPHID": "PHID-USER-zp4gy3jjzfehipsktf6x",
        "repositoryPHID": "PHID-REPO-saax4qdxlbbhahhp2kg5",
        "refs": {
            "branch": {"type": "branch", "name": "default"},
            "base": {
                "type": "base",
                "identifier": "812aabea327f5f5b0c6e611f7603675667da91d3",
            },
        },
        "dateCreated": 1705952725,
        "dateModified": 1705952727,
        "policy": {"view": "public"},
        "baseRevision": "812aabea327f5f5b0c6e611f7603675667da91d3",
    }

    # In this case, the function was not used before the patch.
    print(
        get_function(
            diff1,
            phabricator.load_raw_diff(diff1["id"]),
            "dom/base/nsObjectLoadingContent.cpp",
            "LowerCaseEqualsASCII",
        )
    )

    # In this case, the function was used before the patch.
    print(
        get_function(
            diff1,
            phabricator.load_raw_diff(diff1["id"]),
            "dom/base/nsObjectLoadingContent.cpp",
            "HtmlObjectContentTypeForMIMEType",
        )
    )

    # https://phabricator.services.mozilla.com/D199248?id=811740
    diff2 = {
        "id": 811740,
        "type": "DIFF",
        "phid": "PHID-DIFF-jqafgwh5vndvv5g64np3",
        "attachments": {},
        "revisionPHID": "PHID-DREV-77ddaxlokspj5tgqr5jg",
        "authorPHID": "PHID-USER-vi4mvitknjprtahya3x2",
        "repositoryPHID": "PHID-REPO-saax4qdxlbbhahhp2kg5",
        "refs": {
            "branch": {"type": "branch", "name": "default"},
            "base": {
                "type": "base",
                "identifier": "a89242259d91828b48d64696e67b907469a4349b",
            },
        },
        "dateCreated": 1705943013,
        "dateModified": 1705943015,
        "policy": {"view": "public"},
        "baseRevision": "a89242259d91828b48d64696e67b907469a4349b",
    }

    # In this case, it is a JS file.
    print(
        get_function(
            diff2,
            phabricator.load_raw_diff(diff2["id"]),
            "testing/modules/XPCShellContentUtils.sys.mjs",
            "registerPathHandler",
        )
    )

    patch = """ diff --git a/dom/performance/Performance.cpp b/dom/performance/Performance.cpp
    --- a/dom/performance/Performance.cpp
    +++ b/dom/performance/Performance.cpp
    @@ -952,11 +952,14 @@
    if (!interestedObservers.IsEmpty()) {
    QueueNotificationObserversTask();
    }
    }

    -void Performance::MemoryPressure() { mUserEntries.Clear(); }
    +// We could clear User entries here, but doing so could break sites that call
    +// performance.measure() if the marks disappeared without warning. Chrome
    +// allows "infinite" entries.
    +void Performance::MemoryPressure() {}

    size_t Performance::SizeOfUserEntries(
    mozilla::MallocSizeOf aMallocSizeOf) const {
    size_t userEntries = 0;
    for (const PerformanceEntry* entry : mUserEntries) {

    """
    function_name = "Performance::MemoryPressure"
    path_file = "dom/performance/Performance.cpp"

    print(
        get_function(
            phabricator.search_diffs(diff_id=721783)[0], patch, path_file, function_name
        )
    )

    get_function(
        phabricator.search_diffs(diff_id=736446)[0],
        patch="",
        line=180,
        path="browser/base/content/test/webrtc/browser_devices_select_audio_output.js",
    )
