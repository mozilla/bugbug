from hackbot_agents.build_repair.agent import BuildRepairResult
from hackbot_agents.build_repair.notify import build_email, recipients
from hackbot_agents.build_repair.resolve import PushInfo

HG_REVISION = "341517e50536aabbccddeeff00112233445566"
GIT_REVISION = "7b15e34863cf6b30b613ffadf9d6431fe5a55585"
CULPRIT = "c338a2c1c8d3695b7dec835125af624282555b7e"
TASK_ID = "JfAGrrtoQPS3fXrwZmq1Pg"


def _push(developer_email="dev@mozilla.com"):
    return PushInfo(
        project="autoland",
        hg_revision=HG_REVISION,
        git_commits=[GIT_REVISION, CULPRIT],
        developer_email=developer_email,
    )


def _result(**overrides):
    fields = {
        "git_commit": GIT_REVISION,
        "blamed_commit": CULPRIT,
        "summary": "The build broke on a missing include.",
        "num_turns": 8,
    }
    return BuildRepairResult(**{**fields, **overrides})


def _email(result=None, push=None, **kwargs):
    return build_email(
        result or _result(),
        push or _push(),
        task_id=TASK_ID,
        run_id="1218e630-78c8",
        **kwargs,
    )


def test_the_blamed_author_comes_before_the_pusher():
    assert recipients(_push(), "author@mozilla.com") == [
        "author@mozilla.com",
        "dev@mozilla.com",
    ]


def test_an_unknown_author_leaves_only_the_pusher():
    assert recipients(_push(), None) == ["dev@mozilla.com"]


def test_a_push_with_no_known_pusher_reaches_nobody_individually():
    # The handler still addresses the team.
    assert recipients(_push(developer_email=None), None) == []


def test_the_subject_names_the_repository_and_the_failure_commit():
    subject, _ = _email()
    assert (
        subject
        == f"[build-repair] Build failure analysis for autoland@{GIT_REVISION[:12]}"
    )


def test_the_email_links_every_identifier():
    _, body = _email()
    assert (
        f"[`{GIT_REVISION[:12]}`]"
        f"(https://github.com/mozilla-firefox/firefox/commit/{GIT_REVISION})" in body
    )
    assert (
        f"[`{HG_REVISION[:12]}`]"
        f"(https://hg.mozilla.org/mozilla-unified/rev/{HG_REVISION})" in body
    )
    assert (
        f"[`{TASK_ID}`](https://firefox-ci-tc.services.mozilla.com/tasks/{TASK_ID})"
        in body
    )
    assert "https://hackbot.moz.tools/runs/1218e630-78c8" in body


def test_the_culprit_and_its_author_are_named():
    _, body = _email(blamed_author="author@mozilla.com")
    assert f"**Likely culprit:** [`{CULPRIT[:12]}`]" in body
    assert "by author@mozilla.com" in body
    assert "**author@mozilla.com** authored" in body


def test_a_push_the_agent_cleared_says_so():
    _, body = _email(result=_result(blamed_commit=None))
    assert "Not caused by this push" in body
    assert "Likely culprit" not in body


def test_the_pusher_is_told_why_they_are_on_the_email():
    _, body = _email()
    assert "**dev@mozilla.com** pushed the change whose build failed." in body


def test_agent_prose_nests_under_the_email_headings():
    _, body = _email(result=_result(analysis="# Root cause\n\ndetail"))
    assert "## Analysis" in body
    assert "### Root cause" in body


def test_the_local_build_verification_is_reported_when_known():
    _, body = _email(result=_result(local_build_verified=True))
    assert "- Local build verified: True" in body


def test_no_verification_section_without_a_verdict():
    _, body = _email()
    assert "## Verification" not in body


def test_the_patch_section_frames_a_placeholder_the_apply_step_fills():
    _, body = _email(has_patch=True)
    assert body.endswith("## Proposed patch\n\n```diff\n{patch}\n```")


def test_no_patch_section_without_a_patch():
    _, body = _email()
    assert "Proposed patch" not in body
    assert "{patch}" not in body
