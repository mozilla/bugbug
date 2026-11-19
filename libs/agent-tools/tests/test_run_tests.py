import asyncio
from pathlib import Path

from agent_tools.firefox.tools import run_tests
from agent_tools.firefox.tools.run_tests import wptreport_results


def test_wptreport_results():
    report = {
        "results": [
            {
                "test": "/x.html",
                "status": "OK",
                "subtests": [{"name": "s", "status": "PASS"}],
            },
            {"test": "/ref.html", "status": "FAIL"},
        ]
    }
    assert wptreport_results(report) == {
        "/x.html": {"status": "OK", "subtests": {"s": "PASS"}},
        "/ref.html": {"status": "FAIL", "subtests": {}},
    }


def test_run_tests_passes_paths_and_prefs(tmp_path: Path):
    mach = tmp_path / "mach"
    mach.write_text(
        "#!/bin/sh\n"
        'echo "$@" > args.txt\n'
        'while [ "$1" != "--log-wptreport" ]; do shift; done\n'
        """echo '{"results": [{"test": "/t.html", "status": "PASS"}]}' > "$2"\n"""
    )
    mach.chmod(0o755)
    results = asyncio.run(
        run_tests(
            tmp_path, "wpt", ["dom/x"], tmp_path / "report.json", {"a.b": True, "c": 2}
        )
    )
    assert results == {"/t.html": {"status": "PASS", "subtests": {}}}
    args = (tmp_path / "args.txt").read_text().split()
    assert args[:2] == ["wpt", "--headless"]
    assert args[4:8] == ["--setpref", "a.b=true", "--setpref", "c=2"]
    assert args[-1] == "testing/web-platform/tests/dom/x"


def test_run_tests_without_a_report(tmp_path: Path):
    mach = tmp_path / "mach"
    mach.write_text("#!/bin/sh\necho no tests found\n: > r.json\nexit 1\n")
    mach.chmod(0o755)
    assert asyncio.run(run_tests(tmp_path, "wpt", ["x"], tmp_path / "r.json")) == {}
    assert (tmp_path / "r.log").read_text() == "no tests found\n"
