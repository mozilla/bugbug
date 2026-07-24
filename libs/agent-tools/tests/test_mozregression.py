"""Tests for the mozregression bisection tool."""

import agent_tools.mozregression as mozreg_mod
from agent_tools import mozregression
from agent_tools.claude_sdk import build_sdk_server
from agent_tools.mozregression import (
    MozregressionContext,
    _parse_range,
    run_mozregression,
)
from mcp.types import ListToolsRequest


async def _list(server):
    return (
        await server.request_handlers[ListToolsRequest](
            ListToolsRequest(method="tools/list")
        )
    ).root.tools


async def test_exposes_mozregression_tool():
    ctx = MozregressionContext()
    config = build_sdk_server("mozregression", ctx, mozregression.TOOLS)
    assert config["type"] == "sdk"
    tools = await _list(config["instance"])
    assert {t.name for t in tools} == {"run_mozregression"}


class _FakeProcess:
    def __init__(self, stdout: bytes, stderr: bytes, returncode: int):
        self._stdout = stdout
        self._stderr = stderr
        self.returncode = returncode

    async def communicate(self):
        return self._stdout, self._stderr


_SAMPLE_OUTPUT = b"""\
 0:00.00 INFO: Testing good and bad builds to ensure that they are correct.
 0:10.00 INFO: Last good revision: abcdef1234567
 0:10.00 INFO: First bad revision: 1234567abcdef
 0:10.00 INFO: Pushlog:
https://hg.mozilla.org/integration/autoland/pushloghtml?fromchange=abcdef1234567&tochange=1234567abcdef

 0:10.00 INFO: Looks like the following bug has the changes which introduced the regression:
https://bugzilla.mozilla.org/show_bug.cgi?id=1899999
"""


async def test_parses_range_from_output(monkeypatch):
    captured = {}

    async def fake_exec(*argv, **kwargs):
        captured["argv"] = argv
        captured["env"] = kwargs.get("env")
        return _FakeProcess(_SAMPLE_OUTPUT, b"", 0)

    monkeypatch.setattr(mozreg_mod.asyncio, "create_subprocess_exec", fake_exec)

    ctx = MozregressionContext(anthropic_api_key="sk-test")
    result = await run_mozregression(
        ctx,
        good="123",
        bad="124",
        prompt="Open the page. GOOD if it loads, BAD if it errors.",
        url="https://example.com",
        prefs={"foo.bar": "true"},
    )

    assert result["success"] is True
    assert result["last_good"] == "abcdef1234567"
    assert result["first_bad"] == "1234567abcdef"
    assert result["pushlog_url"].startswith("https://hg.mozilla.org/")
    assert result["regressor_bug"] == 1899999

    argv = captured["argv"]
    assert "--prompt" in argv
    assert "--good" in argv and "123" in argv
    assert "--bad" in argv and "124" in argv
    assert "--arg" in argv and "https://example.com" in argv
    assert "--pref" in argv and "foo.bar:true" in argv
    assert "--prompt-headless" in argv
    # The Anthropic key is injected for the nested claude CLI.
    assert captured["env"]["ANTHROPIC_API_KEY"] == "sk-test"


async def test_nonzero_exit_never_raises(monkeypatch):
    async def fake_exec(*argv, **kwargs):
        return _FakeProcess(b"some progress\n", b"boom\n", 2)

    monkeypatch.setattr(mozreg_mod.asyncio, "create_subprocess_exec", fake_exec)

    ctx = MozregressionContext()
    result = await run_mozregression(
        ctx, good="2024-01-01", bad="2024-02-01", prompt="check"
    )

    assert result["success"] is False
    assert result["returncode"] == 2
    assert result["last_good"] is None
    assert "exited with code 2" in result["message"]


# Trimmed from a real `--find-fix` run: several intermediate Pushlog lines while
# narrowing, then the final range. find-fix reports "First good"/"Last bad".
_FIND_FIX_OUTPUT = """\
 0:00 INFO: Testing good and bad builds.
 0:10 INFO: Pushlog:
https://hg.mozilla.org/mozilla-central/pushloghtml?fromchange=aaaaaaaaaaaa&tochange=bbbbbbbbbbbb
 5:00 INFO: Agent verdict: build is good
 5:01 INFO: Narrowed integration fix window ...
 5:01 INFO: Pushlog:
https://hg.mozilla.org/integration/autoland/pushloghtml?fromchange=f8744ce82ac4&tochange=3173defff923
 9:00 INFO: Agent verdict: build is bad
 9:01 INFO: No more integration revisions, bisection finished.
 9:01 INFO: First good revision: 3173defff92364ba83f3535ca8f751720dd14eda
 9:01 INFO: Last bad revision: d39b1b8814837efcc71cebc60cc1ea2f94b135de
 9:01 INFO: Pushlog:
https://hg.mozilla.org/integration/autoland/pushloghtml?fromchange=d39b1b881483&tochange=3173defff923
"""


def test_parse_range_find_fix_wording_and_last_pushlog():
    parsed = _parse_range(_FIND_FIX_OUTPUT)
    # find-fix wording is understood...
    assert parsed["last_good"] == "3173defff92364ba83f3535ca8f751720dd14eda"
    assert parsed["first_bad"] == "d39b1b8814837efcc71cebc60cc1ea2f94b135de"
    # ...and the LAST (final) pushlog wins over the intermediate ones.
    assert parsed["pushlog_url"].endswith(
        "fromchange=d39b1b881483&tochange=3173defff923"
    )


def test_parse_range_pushlog_fallback_when_no_labelled_lines():
    text = (
        "INFO: Pushlog:\n"
        "https://hg.mozilla.org/mozilla-central/pushloghtml?"
        "fromchange=1111111111&tochange=2222222222\n"
    )
    parsed = _parse_range(text)
    assert parsed["last_good"] == "1111111111"
    assert parsed["first_bad"] == "2222222222"


async def test_missing_executable_returns_error(monkeypatch):
    async def fake_exec(*argv, **kwargs):
        raise FileNotFoundError("mozregression")

    monkeypatch.setattr(mozreg_mod.asyncio, "create_subprocess_exec", fake_exec)

    ctx = MozregressionContext(executable="mozregression")
    result = await run_mozregression(ctx, good="1", bad="2", prompt="check")

    assert result["success"] is False
    assert "not found on PATH" in result["message"]
