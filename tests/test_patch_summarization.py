# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from unidiff import PatchSet


def _make_tool():
    """Build a PatchSummarizationTool without running __init__ (avoids create_agent)."""
    pytest.importorskip("langchain")
    from bugbug.tools.patch_summarization.agent import PatchSummarizationTool

    tool = PatchSummarizationTool.__new__(PatchSummarizationTool)
    tool.target_software = "Mozilla Firefox"
    tool.agent = MagicMock()
    return tool


def _make_patch():
    patch_set = PatchSet.from_string(
        "--- a/f.txt\n+++ b/f.txt\n@@ -0,0 +1,2 @@\n+a\n+b\n"
    )
    return SimpleNamespace(
        patch_set=patch_set,
        has_bug=False,
        bug_title="N/A",
        patch_title="Add a and b",
        patch_description="",
    )


def test_run_handles_list_content_with_budget_marker():
    # Extended-thinking responses return .content as a list of blocks, not a
    # plain str (issue #6517); the fix must still work with the pre-existing
    # token-budget marker stripping (FIXME #5705).
    pytest.importorskip("langchain")
    from langchain.messages import AIMessage

    tool = _make_tool()
    tool.agent.invoke = MagicMock(
        return_value={
            "messages": [
                AIMessage(
                    content=[
                        {"type": "thinking", "thinking": "internal reasoning"},
                        {"type": "text", "text": "Adds a and b.<budget:token 123>"},
                    ]
                )
            ]
        }
    )

    summary = tool.run(_make_patch())

    assert summary == "Adds a and b."


def test_run_still_handles_plain_string_content():
    pytest.importorskip("langchain")
    from langchain.messages import AIMessage

    tool = _make_tool()
    tool.agent.invoke = MagicMock(
        return_value={"messages": [AIMessage(content="Adds a and b.")]}
    )

    summary = tool.run(_make_patch())

    assert summary == "Adds a and b."
