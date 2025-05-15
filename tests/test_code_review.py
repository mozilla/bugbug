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


def test_generate_processed_output_attaches_comment_to_correct_line():
    # Regression test for https://github.com/mozilla/bugbug/issues/4643
    import json

    from unidiff import PatchSet

    from bugbug.tools.code_review import InlineComment, generate_processed_output

    # Mock output from the model
    output = json.dumps(
        [{"file": "example.py", "code_line": 10, "comment": "This is a test comment."}]
    )

    # Create a mock patch set
    patch_content = """\
diff --git a/example.py b/example.py
--- a/example.py
+++ b/example.py
@@ -8,0 +9,2 @@
+def example_function():
+    pass
"""
    patch = PatchSet(patch_content)

    # Generate processed output
    comments = list(generate_processed_output(output, patch))

    # Check that the comment is attached to the correct line
    assert len(comments) == 1
    assert isinstance(comments[0], InlineComment)
    assert comments[0].filename == "example.py"
    assert comments[0].start_line == 10
    assert comments[0].end_line == 10
    assert comments[0].content == "This is a test comment."
