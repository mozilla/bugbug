import asyncio
from unittest import mock

import pytest
from hackbot_agents.autowebcompat_etp_compare import agent
from hackbot_agents.autowebcompat_etp_compare.capture import (
    Capture,
    parse_blocked_trackers,
)
from hackbot_agents.autowebcompat_etp_compare.compare import diff_screenshots
from hackbot_agents.autowebcompat_etp_compare.result import (
    ComparisonVerdict,
    ScreenshotDiff,
)
from hackbot_runtime import AgentError
from PIL import Image
from pydantic import ValidationError


def save_png(path, size=(20, 30), color="white", box=None):
    image = Image.new("RGB", size, color)
    if box is not None:
        image.paste((0, 0, 1), box)
    image.save(path)
    return path


def test_diff_identical(tmp_path):
    a = save_png(tmp_path / "a.png")
    b = save_png(tmp_path / "b.png")
    diff = diff_screenshots(a, b, tmp_path / "diff.png")
    assert diff.identical
    assert not (tmp_path / "diff.png").exists()


def test_diff_changed_region(tmp_path):
    a = save_png(tmp_path / "a.png")
    # One channel off by one still counts.
    b = save_png(tmp_path / "b.png", box=(5, 10, 10, 20))
    diff = diff_screenshots(a, b, tmp_path / "diff.png")
    assert not diff.identical
    assert diff.bbox == (5, 10, 10, 20)
    assert diff.changed_fraction == pytest.approx(50 / 600)
    with Image.open(tmp_path / "diff.png") as image:
        assert image.getpixel((7, 15)) == (255, 0, 0)
        assert image.getpixel((0, 0)) != (255, 0, 0)


def test_diff_different_size(tmp_path):
    a = save_png(tmp_path / "a.png")
    b = save_png(tmp_path / "b.png", size=(20, 40))
    diff = diff_screenshots(a, b, tmp_path / "diff.png")
    assert not diff.identical
    assert diff.bbox is None
    assert diff.size == (20, 40)
    assert diff.reference_size == (20, 30)
    assert not (tmp_path / "diff.png").exists()


def test_parse_blocked_trackers():
    log = (
        "Content blocking log for https://example.com/:\n"
        "Blocked trackers: 2 (https://a.com, https://b.com)\n{}"
    )
    assert parse_blocked_trackers(log) == (2, ["https://a.com", "https://b.com"])
    assert parse_blocked_trackers("Blocked trackers: 0\n{}") == (0, [])
    with pytest.raises(AgentError):
        parse_blocked_trackers("Error: nope")


def test_verdict_broken_needs_configurations():
    with pytest.raises(ValidationError):
        ComparisonVerdict(outcome="broken", broken_configurations=[], summary="")
    with pytest.raises(ValidationError):
        ComparisonVerdict(
            outcome="sites_same", broken_configurations=["strict"], summary=""
        )
    with pytest.raises(ValidationError):
        ComparisonVerdict(
            outcome="broken", broken_configurations=["easylist"], summary=""
        )
    with pytest.raises(ValidationError):
        ComparisonVerdict(
            outcome="sites_same",
            broken_configurations=[],
            breakage_tags={"strict": ["script-error"]},
            summary="",
        )
    with pytest.raises(ValidationError):
        ComparisonVerdict(
            outcome="broken",
            broken_configurations=["strict", "easylist"],
            breakage_tags={"easylist": ["script-error"]},
            summary="",
        )
    with pytest.raises(ValidationError):
        ComparisonVerdict(
            outcome="broken",
            broken_configurations=["easylist"],
            breakage_tags={"easylist": [" "]},
            summary="",
        )
    verdict = ComparisonVerdict(
        outcome="broken",
        broken_configurations=["easyprivacy", "easylist"],
        breakage_tags={
            "easyprivacy": ["script_error"],
            "easylist": ["Anti Adblocker", "script_error", "anti-adblocker", " "],
        },
        summary="",
    )
    assert verdict.breakage_tags == {
        "easyprivacy": ["script-error"],
        "easylist": ["anti-adblocker", "script-error"],
    }


def make_captures(tmp_path, counts, failed=()):
    return {
        configuration: Capture(
            configuration=configuration,
            screenshot=save_png(tmp_path / f"{configuration}.png"),
            blocked_trackers=count,
            blocked_tracker_origins=[f"https://t{i}.com" for i in range(count)],
            load_status="loaded",
            navigation_failed=configuration in failed,
        )
        for configuration, count in counts.items()
    }


COUNTS = {"off": 0, "strict": 3, "easyprivacy": 2, "easylist": 5}
SAME = ScreenshotDiff(identical=True, size=(1, 1), reference_size=(1, 1))


def test_needs_review(tmp_path):
    captures = make_captures(tmp_path, COUNTS)
    same = dict.fromkeys(("strict", "easyprivacy", "easylist"), SAME)
    assert not agent.needs_review(captures, same)
    changed = same | {"easylist": SAME.model_copy(update={"identical": False})}
    assert agent.needs_review(captures, changed)
    # The same error page everywhere is not "the same site".
    failed = make_captures(tmp_path, COUNTS, failed=("off",))
    assert agent.needs_review(failed, same)


def test_identical_screenshots_skip_the_agent(tmp_path):
    shots = tmp_path / "shots"
    shots.mkdir()
    captures = make_captures(shots, COUNTS)

    def publish(key, path, content_type):
        return key

    with (
        mock.patch.object(agent, "install_firefox"),
        mock.patch.object(agent, "build_firefox_devtools_server"),
        mock.patch.object(agent, "capture_configurations", return_value=captures),
        mock.patch.object(agent, "EtpComparison") as task,
        mock.patch.object(agent.tempfile, "mkdtemp", return_value=str(tmp_path)),
    ):
        result = asyncio.run(
            agent.run_autowebcompat_etp_compare(
                agent.TaskConfig(headless=True),
                agent.RunTracker(),
                "https://example.com/",
                publish,
            )
        )

    task.assert_not_called()
    assert result.outcome == "sites_same"
    assert not result.llm_used
    assert result.broken_configurations == []
    assert result.blocked_trackers == COUNTS
    assert result.blocked_tracker_origins["strict"] == [
        "https://t0.com",
        "https://t1.com",
        "https://t2.com",
    ]
    assert all(diff.identical for diff in result.screenshot_diffs.values())
    assert set(result.screenshot_urls) == set(COUNTS)
    assert "Strict ETP: 3" in result.summary
