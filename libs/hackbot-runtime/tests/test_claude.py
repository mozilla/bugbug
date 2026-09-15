"""Tests for the shared claude-agent-sdk Reporter (hackbot_runtime.claude)."""

from hackbot_runtime.claude import Reporter, _truncate


def test_truncate_short_string_unchanged():
    assert _truncate("hello", 10) == "hello"


def test_truncate_long_string_marks_remainder():
    out = _truncate("x" * 20, 5)
    assert out.startswith("xxxxx")
    assert "15 more chars" in out


def test_header_writes_banner_to_log(tmp_path):
    log = tmp_path / "agent.log"
    with Reporter(verbose=False, log_path=log) as reporter:
        reporter.header("bug 12345")
    contents = log.read_text()
    assert "# bug 12345" in contents
    assert "#" * 60 in contents


def test_header_always_prints_even_when_not_verbose(capsys):
    with Reporter(verbose=False, log_path=None) as reporter:
        reporter.header("bug 999")
    out = capsys.readouterr().out
    assert "# bug 999" in out


def test_no_log_file_when_path_is_none(tmp_path):
    # Should not raise and should not create any file.
    with Reporter(verbose=True, log_path=None) as reporter:
        reporter.header("section")
    assert not list(tmp_path.iterdir())
