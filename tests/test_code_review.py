import requests
import responses
from unidiff import PatchSet

from bugbug.tools.code_review import find_comment_scope


def test_find_comment_scope():
    responses.add_passthru("https://phabricator.services.mozilla.com/")
    responses.add_passthru(
        "https://mozphab-phabhost-cdn.devsvcprod.mozaws.net/file/data/"
    )

    test_data = {
        "https://phabricator.services.mozilla.com/D233024?id=964198": {
            "browser/components/newtab/test/browser/browser.toml": {
                79: {
                    "line_start": 78,
                    "line_end": 79,
                    "has_added_lines": False,
                }
            },
            "browser/components/asrouter/tests/browser/browser.toml": {
                63: {
                    "line_start": 60,
                    "line_end": 74,
                    "has_added_lines": True,
                },
            },
        },
        "https://phabricator.services.mozilla.com/D240754?id=995999": {
            "dom/canvas/WebGLShaderValidator.cpp": {
                39: {
                    "line_start": 37,
                    "line_end": 42,
                    "has_added_lines": True,
                },
                46: {
                    "line_start": 37,
                    "line_end": 42,
                    "has_added_lines": True,
                },
            }
        },
    }

    for revision_url, patch_files in test_data.items():
        print(f"## Testing {revision_url} ##")
        resp = requests.get(revision_url + "&download=true", timeout=5)
        resp.raise_for_status()
        raw_diff = resp.text
        print("## Raw Diff ##")
        print(raw_diff)
        patch_set = PatchSet.from_string(raw_diff)
        print("## Patch Set ##")
        print(patch_set)

        for file_name, target_hunks in patch_files.items():
            print(f"## Testing {file_name} ##")
            patched_file = next(
                patched_file
                for patched_file in patch_set
                if patched_file.path == file_name
            )

            for line_number, expected_scope in target_hunks.items():
                assert find_comment_scope(patched_file, line_number) == expected_scope
