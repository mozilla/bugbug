import requests
import responses
from unidiff import PatchSet

from bugbug.tools.code_review import find_comment_scope


def test_find_comment_scope():
    responses.add_passthru("https://phabricator.services.mozilla.com/")
    responses.add_passthru("https://d2mfgivbiy2fiw.cloudfront.net/file/data/")

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
        raw_diff = requests.get(revision_url + "&download=true", timeout=5).text
        patch_set = PatchSet.from_string(raw_diff)

        for file_name, target_hunks in patch_files.items():
            patched_file = next(
                patched_file
                for patched_file in patch_set
                if patched_file.path == file_name
            )

            for line_number, expected_scope in target_hunks.items():
                assert find_comment_scope(patched_file, line_number) == expected_scope
