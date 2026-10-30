import asyncio
import json
import subprocess
from types import SimpleNamespace

import pytest
from agent_tools.registry import ToolError
from hackbot_agents.test_repair import agent
from hackbot_agents.test_repair.prompts import MAX_TESTS_PER_GROUP
from hackbot_agents.test_repair.resolve import (
    CommitRange,
    FailingGroup,
    Investigation,
)
from hackbot_runtime import ActionsRecorder, AgentError
from hackbot_runtime.actions import agents

HANDOFF_TOOL = "mcp__actions__agents_start_run"


def _result_msg(is_error=False):
    return SimpleNamespace(
        is_error=is_error,
        total_cost_usd=0.1,
        num_turns=3,
        result="max turns" if is_error else None,
        subtype=None,
    )


def _flat(text):
    return " ".join(text.split())


def _git_repo(path):
    """A two-commit repo standing in for the pinned shallow checkout.

    The culprit is validated with ``git rev-parse``, so the tests need real
    commits. Returns [base, head].
    """

    def git(*args):
        return subprocess.run(
            ["git", "-C", str(path), *args],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    subprocess.run(["git", "init", "-q", str(path)], check=True, capture_output=True)
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "T")
    shas = []
    for name in ("base", "head"):
        (path / f"{name}.txt").write_text(name)
        git("add", "-A")
        git("commit", "-qm", name)
        shas.append(git("rev-parse", "HEAD"))
    return shas


def _investigation(
    head,
    platform="linux1804-64/opt",
    groups=None,
    group_based=True,
    label="test-linux1804-64/opt-mochitest-browser-chrome-swr-1",
    known_intermittent_bugs=None,
):
    return Investigation(
        project="autoland",
        hg_revision="hgrev",
        harness="mochitest",
        platform=platform,
        failing_groups=groups
        if groups is not None
        else [FailingGroup("dom/base/test/mochitest.ini", ["dom/base/test/a.js"])],
        commit_range=CommitRange(head=head, span=2),
        label=label,
        group_based=group_based,
        known_intermittent_bugs=known_intermittent_bugs or [],
    )


def _run(
    tmp_path,
    verdicts,
    monkeypatch,
    results=None,
    platform="linux1804-64/opt",
    groups=None,
    group_based=True,
    known_intermittent_bugs=None,
    options_out=None,
    actions_recorder=None,
    hand_off=False,
    published=None,
):
    repo = tmp_path / "src"
    repo.mkdir()
    base, head = _git_repo(repo)
    scratch_out = tmp_path / "out"
    scratch_out.mkdir()
    calls = []

    async def fake_session(reporter, options, prompt):
        calls.append(prompt)
        # A compliant run fetches the log into scratch_out/logs; the agent loop
        # fails the run when nothing is there (bug 6665).
        job = scratch_out / "logs" / "job_1"
        job.mkdir(parents=True, exist_ok=True)
        (job / "live_backing_log.log").write_text("TEST-UNEXPECTED-FAIL | a.js")
        if options_out is not None:
            options_out.append(options)
        verdict = verdicts.pop(0)
        if verdict is not None:
            if verdict.get("culprit_commit") == "HEAD":
                verdict = {**verdict, "culprit_commit": head}
            if verdict.get("candidate_commits"):
                verdict = {
                    **verdict,
                    "candidate_commits": [
                        {"HEAD": head, "BASE": base}.get(sha, sha)
                        for sha in verdict["candidate_commits"]
                    ],
                }
            (scratch_out / "verdict.json").write_text(json.dumps(verdict))
        (scratch_out / "summary.md").write_text("the verdict")
        (scratch_out / "analysis.md").write_text("the reasoning")
        if hand_off:
            await agents.start_run(
                actions_recorder, agent="test-repair-fix", reasoning="culprit found"
            )
        return _result_msg(is_error=bool(results and results.pop(0)))

    monkeypatch.setattr(agent, "_run_session", fake_session)

    def publish_file(key, path, content_type=None):
        if published is not None:
            published.append(key)
        return key

    result = asyncio.run(
        agent.run_test_repair(
            bugzilla_mcp_server=None,
            source_repo=repo,
            investigation=_investigation(
                head,
                platform,
                groups,
                group_based,
                known_intermittent_bugs=known_intermittent_bugs,
            ),
            scratch_out=scratch_out,
            verbose=False,
            log=None,
            publish_file=publish_file,
            actions_recorder=actions_recorder,
        )
    )
    return result, calls, head


def test_no_culprit_means_no_backout(tmp_path, monkeypatch):
    result, calls, _head = _run(
        tmp_path,
        [{"recommendation": "backout", "culprit_commit": None, "confidence": 0.3}],
        monkeypatch,
    )
    assert result.culprit_commit is None
    assert result.recommendation == "do_not_backout"


# --- handing a culprit on to test-repair-fix ------------------------------- #


def _blamed():
    return [{"recommendation": "backout", "culprit_commit": "HEAD", "confidence": 0.9}]


def test_the_hand_off_is_offered_with_a_recorder(tmp_path, monkeypatch):
    options = []
    _, calls, _ = _run(
        tmp_path,
        _blamed(),
        monkeypatch,
        options_out=options,
        actions_recorder=ActionsRecorder(),
    )
    assert HANDOFF_TOOL in options[0].allowed_tools
    assert "actions" in options[0].mcp_servers
    assert 'agent "test-repair-fix"' in _flat(calls[0])
    assert "do not plan or attempt the fix" in _flat(calls[0])


def test_the_analysis_neither_edits_nor_builds(tmp_path, monkeypatch):
    options = []
    _run(tmp_path, _blamed(), monkeypatch, options_out=options)
    assert not {"Edit", "MultiEdit"} & set(options[0].allowed_tools)
    assert options[0].add_dirs == [str(tmp_path)]
    assert "git diff --stat" not in options[0].system_prompt["append"]


@pytest.mark.parametrize("other", ["bug-fix", "build-repair", "test-repair"])
def test_the_analysis_can_only_start_the_fix_agent(tmp_path, monkeypatch, other):
    recorder = ActionsRecorder()
    _run(tmp_path, _blamed(), monkeypatch, actions_recorder=recorder)
    with pytest.raises(ToolError, match="allowed: test-repair-fix"):
        asyncio.run(agents.start_run(recorder, agent=other, reasoning="why"))


def test_no_hand_off_without_a_recorder(tmp_path, monkeypatch):
    options = []
    _, calls, _ = _run(tmp_path, _blamed(), monkeypatch, options_out=options)
    assert HANDOFF_TOOL not in options[0].allowed_tools
    assert "actions" not in options[0].mcp_servers
    assert "test-repair-fix" not in calls[0]


def test_the_analysis_never_builds(tmp_path, monkeypatch):
    options = []
    _run(
        tmp_path,
        _blamed(),
        monkeypatch,
        options_out=options,
        actions_recorder=ActionsRecorder(),
    )
    assert "firefox" not in options[0].mcp_servers
    assert not any("firefox" in tool for tool in options[0].allowed_tools)


def test_a_hand_off_publishes_what_the_fix_works_from(tmp_path, monkeypatch):
    recorder = ActionsRecorder()
    published = []
    result, _, head = _run(
        tmp_path,
        _blamed(),
        monkeypatch,
        actions_recorder=recorder,
        hand_off=True,
        published=published,
    )
    assert result.culprit_commit == head
    assert [a["type"] for a in recorder.actions] == ["agents.start_run"]
    assert {
        "summary.md",
        "analysis.md",
        "verdict.json",
        "logs/job_1/live_backing_log.log",
    } <= set(published)


def test_a_hand_off_without_a_culprit_is_dropped(tmp_path, monkeypatch):
    recorder = ActionsRecorder()
    published = []
    _run(
        tmp_path,
        [{"recommendation": "backout", "culprit_commit": None}],
        monkeypatch,
        actions_recorder=recorder,
        hand_off=True,
        published=published,
    )
    assert recorder.actions == []
    assert "verdict.json" not in published


def test_no_hand_off_publishes_no_logs(tmp_path, monkeypatch):
    published = []
    _run(
        tmp_path,
        _blamed(),
        monkeypatch,
        actions_recorder=ActionsRecorder(),
        published=published,
    )
    assert not any(key.startswith("logs/") for key in published)


def test_the_prompt_names_the_checkout_path(tmp_path, monkeypatch):
    # Only the scratch dir used to be named, so the agent cd'd there and then had
    # to hunt the filesystem for the tree.
    _result, calls, _head = _run(tmp_path, _blamed(), monkeypatch)
    assert str(tmp_path / "src") in calls[0]


def test_tree_searches_are_steered_to_git_grep(tmp_path, monkeypatch):
    # An unbounded `grep -r` over the Firefox tree burns the whole Bash timeout.
    options = []
    _run(tmp_path, _blamed(), monkeypatch, options_out=options)
    assert "`git grep`" in options[0].system_prompt["append"]


def test_hallucinated_culprit_is_discarded(tmp_path, monkeypatch):
    result, calls, _head = _run(
        tmp_path,
        [{"recommendation": "backout", "culprit_commit": "deadbeefdeadbeef"}],
        monkeypatch,
    )
    assert len(calls) == 1
    assert result.culprit_commit is None
    assert result.recommendation == "do_not_backout"


def test_range_prompt_does_not_assert_the_culprit(tmp_path, monkeypatch):
    # The range is always depth-bounded rather than anchored on a green run, so the
    # prompt must not claim the culprit is inside it.
    _result, calls, _head = _run(tmp_path, [{"culprit_commit": None}], monkeypatch)
    assert "may predate" in calls[0]
    assert "git log --format='%h %ci %s' HEAD~2..HEAD" in calls[0]


def test_assemble_defaults_on_empty_verdict(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    result = agent._assemble_result(
        out,
        verdict={},
        source_repo=tmp_path,
        total_turns=1,
        total_cost=0.0,
        publish_file=None,
    )
    # A regression is assumed, but there is no culprit to back out.
    assert result.classification == "regression"
    assert result.recommendation == "do_not_backout"


def test_assemble_tolerates_malformed_verdict_fields(tmp_path):
    # verdict.json is model-authored; bad fields must not crash a finished run.
    out = tmp_path / "out"
    out.mkdir()
    result = agent._assemble_result(
        out,
        verdict={
            "recommendation": "backout",
            "culprit_commit": "abc",
            "confidence": "high",
        },
        source_repo=tmp_path,
        total_turns=1,
        total_cost=0.0,
        publish_file=None,
    )
    assert result.confidence == 0.0
    assert result.culprit_bug is None
    assert result.classification == "regression"
    # "abc" resolves to no commit in the checkout, so the blame is dropped.
    assert result.culprit_commit is None


def test_assemble_reports_intermittent_classification(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    result = agent._assemble_result(
        out,
        verdict={"classification": "intermittent", "intermittent_bug": 42},
        source_repo=tmp_path,
        total_turns=1,
        total_cost=0.0,
        publish_file=None,
    )
    assert result.classification == "intermittent"
    assert result.intermittent_bug == 42
    assert result.recommendation == "do_not_backout"


def test_coerce_recommendation_defaults_by_classification():
    assert agent._coerce_recommendation("bogus", "regression", True) == "backout"
    assert (
        agent._coerce_recommendation("bogus", "intermittent", False) == "do_not_backout"
    )
    # "backout" is meaningless without a commit to back out.
    assert agent._coerce_recommendation("backout", "regression", False) == (
        "do_not_backout"
    )


def test_analysis_prompt_frames_the_recommendation_as_the_sheriff_action(
    tmp_path, monkeypatch
):
    _result, calls, _head = _run(tmp_path, [{"culprit_commit": None}], monkeypatch)
    assert '"backout", "do_not_backout" or "rerun"' in _flat(calls[0])
    assert "land_fix" not in calls[0]
    assert "sheriff's action" in calls[0]


def test_analysis_prompt_asks_for_the_whole_stack(tmp_path, monkeypatch):
    # A culprit at the bottom of a stack cannot be backed out on its own; the
    # commits above it were written against the broken change.
    _result, calls, _head = _run(tmp_path, [{"culprit_commit": None}], monkeypatch)
    assert "`Bug NNNNNN`" in calls[0]
    assert "whole stack has to be backed out" in _flat(calls[0])


def test_analysis_prompt_rules_out_fixing_forward(tmp_path, monkeypatch):
    _result, calls, _head = _run(tmp_path, [{"culprit_commit": None}], monkeypatch)
    assert 'there is no "land a fix instead"' in _flat(calls[0])
    assert '"small enough to fix forward" is not a reason' in _flat(calls[0])


def test_resolve_culprit_normalizes_against_the_checkout(tmp_path):
    repo = tmp_path / "src"
    repo.mkdir()
    _base, head = _git_repo(repo)
    assert agent._resolve_culprit(repo, head) == head
    assert agent._resolve_culprit(repo, head[:8]) == head
    assert agent._resolve_culprit(repo, "deadbeefdeadbeefdeadbeef") is None
    assert agent._resolve_culprit(repo, None) is None
    assert agent._resolve_culprit(repo, "  ") is None


def test_range_expr_is_relative_to_the_checkout():
    assert agent._range_expr(CommitRange("h" * 40, 5)) == "HEAD~5..HEAD"


def test_all_failing_tests_are_listed(tmp_path, monkeypatch):
    tests = [f"dom/base/test/test_{i}.js" for i in range(3)]
    _result, calls, _head = _run(
        tmp_path,
        [{"culprit_commit": None}],
        monkeypatch,
        groups=[FailingGroup("dom/base/test/mochitest.ini", tests)],
    )
    for test in tests:
        assert test in calls[0]


def test_long_failing_test_lists_are_elided(tmp_path, monkeypatch):
    tests = [f"dom/base/test/test_{i}.js" for i in range(MAX_TESTS_PER_GROUP + 5)]
    _result, calls, _head = _run(
        tmp_path,
        [{"culprit_commit": None}],
        monkeypatch,
        groups=[FailingGroup("dom/base/test/mochitest.ini", tests)],
    )
    assert "+5 more" in calls[0]


def test_group_less_suites_say_so_instead_of_reporting_a_lookup_failure(
    tmp_path, monkeypatch
):
    # gtest and friends report no manifests at all, which is not the same thing as
    # mozci failing to resolve them.
    _result, calls, _head = _run(
        tmp_path,
        [{"culprit_commit": None}],
        monkeypatch,
        groups=[],
        group_based=False,
    )
    assert "does not report test manifests" in calls[0]
    assert "could not be resolved" not in calls[0]


def test_prompt_names_the_task_not_just_the_platform(tmp_path, monkeypatch):
    # The variant and chunk live in the label, so a config-specific failure is only
    # visible to the agent if the label is.
    _result, calls, _head = _run(tmp_path, [{"culprit_commit": None}], monkeypatch)
    assert "test-linux1804-64/opt-mochitest-browser-chrome-swr-1" in calls[0]


def test_prompt_treats_path_filtering_as_ordering_not_exclusion(tmp_path, monkeypatch):
    _result, calls, _head = _run(tmp_path, [{"culprit_commit": None}], monkeypatch)
    assert "never clears anyone" in _flat(calls[0])
    assert "before concluding nothing explains the failure" in _flat(calls[0])


def test_prompt_does_not_presume_intermittents_were_filtered_out(tmp_path, monkeypatch):
    # Known intermittents do reach the agent, so finding the tracking bug is part
    # of the job rather than something to rule out only if the logs insist.
    _result, calls, _head = _run(tmp_path, [{"culprit_commit": None}], monkeypatch)
    assert "listener" not in calls[0].lower()
    assert "known intermittent" in calls[0]
    assert "intermittent_bug" in calls[0]


def test_treeherder_matched_bugs_are_listed_when_any_matched(tmp_path, monkeypatch):
    _result, calls, _head = _run(
        tmp_path,
        [{"culprit_commit": None}],
        monkeypatch,
        known_intermittent_bugs=[1805760, 2016093],
    )
    assert "1805760, 2016093" in calls[0]
    assert "before blaming a commit" in calls[0]


def test_prompt_omits_the_treeherder_line_without_matched_bugs(tmp_path, monkeypatch):
    # Treeherder matches nothing for plenty of failures; the prompt must not say so.
    _result, calls, _head = _run(tmp_path, [{"culprit_commit": None}], monkeypatch)
    assert "Treeherder already matches" not in calls[0]


def test_candidate_commits_are_kept_when_no_culprit_convinces(tmp_path, monkeypatch):
    result, _calls, head = _run(
        tmp_path,
        [{"culprit_commit": None, "candidate_commits": ["HEAD", "BASE"]}],
        monkeypatch,
    )
    assert result.culprit_commit is None
    # Ranked order is the model's; both are real commits in the range.
    assert result.candidate_commits[0] == head
    assert len(result.candidate_commits) == 2


def test_candidate_commits_drop_hallucinations_and_the_culprit(tmp_path):
    repo = tmp_path / "src"
    repo.mkdir()
    base, head = _git_repo(repo)
    # The culprit is not repeated in the list, invented shas go, duplicates collapse.
    assert agent._resolve_candidates(
        repo, [head, base, base, "deadbeefdeadbeefdeadbeef", 42], head
    ) == [base]


def test_candidate_commits_tolerate_a_non_list(tmp_path):
    repo = tmp_path / "src"
    repo.mkdir()
    _git_repo(repo)
    assert agent._resolve_candidates(repo, "not a list", None) == []
    assert agent._resolve_candidates(repo, None, None) == []


def test_candidate_commits_are_capped(tmp_path):
    repo = tmp_path / "src"
    repo.mkdir()
    base, head = _git_repo(repo)
    shas = [head, base] * 10
    resolved = agent._resolve_candidates(repo, shas, None)
    assert len(resolved) <= agent.MAX_CANDIDATE_COMMITS
    assert resolved == [head, base]


def test_rerun_recommendation_survives_without_a_culprit(tmp_path, monkeypatch):
    result, calls, _head = _run(
        tmp_path,
        [{"recommendation": "rerun", "culprit_commit": None, "confidence": 0.2}],
        monkeypatch,
    )
    assert len(calls) == 1
    assert result.recommendation == "rerun"


def test_the_hg_revision_is_labelled_as_one(tmp_path, monkeypatch):
    # Every other revision in the prompt is a git hash; an unlabelled hg node
    # makes the agent run git commands against it and get exit 128.
    _result, calls, _head = _run(tmp_path, [{"culprit_commit": None}], monkeypatch)
    assert "hg revision" in _flat(calls[0])
    assert "never to git" in _flat(calls[0])


def test_analysis_prompt_offers_treeherder_cli(tmp_path, monkeypatch):
    _result, calls, _head = _run(
        tmp_path,
        [{"recommendation": "backout", "culprit_commit": "HEAD", "confidence": 0.5}],
        monkeypatch,
    )
    prompt = _flat(calls[0])
    # The command has to be runnable as printed: repo and revision, not placeholders.
    assert "treeherder-cli hgrev --repo autoland" in prompt
    assert "--suspects" in prompt
    assert "--similar-history" in prompt
    # The manifest timeline is the only reliable history; it also settles whether the
    # failure predates the range, in which case no culprit is hunted.
    assert "--group-history <manifest>" in prompt
    assert "predates the window" in prompt
    assert "Do not hunt for a culprit outside the range" in prompt
    # Raw API calls and open-ended searching are what made runs take 40+ turns.
    assert "Never query the Treeherder API" in prompt
    assert "Stop as soon as one of these holds" in prompt
    assert "in one Bash" in prompt
    # The two ways to waste a run: unbounded output and a blocking flag.
    assert "Always pass `--filter`" in prompt
    assert "Never pass `--watch`" in prompt


def test_a_reported_blocker_fails_the_run(tmp_path, monkeypatch):
    # The agent could not get the log, so there is nothing to analyse. Failing the
    # run is what reaches the API as an error and skips the Slack notification;
    # a verdict here would be a fix invented from the diff alone (bug 6665).
    repo = tmp_path / "src"
    repo.mkdir()
    _git_repo(repo)
    scratch_out = tmp_path / "out"
    scratch_out.mkdir()

    async def fake_session(reporter, options, prompt):
        (scratch_out / "error.txt").write_text(
            "treeherder-cli --fetch-logs returned no jobs for "
            "test-linux1804-64/opt-mochitest-1"
        )
        return _result_msg()

    monkeypatch.setattr(agent, "_run_session", fake_session)

    with pytest.raises(AgentError) as excinfo:
        asyncio.run(
            agent.run_test_repair(
                bugzilla_mcp_server=None,
                source_repo=repo,
                investigation=_investigation("headsha"),
                scratch_out=scratch_out,
                verbose=False,
                log=None,
            )
        )
    assert "returned no jobs" in str(excinfo.value)


def test_a_verdict_without_a_retrieved_log_fails_the_run(tmp_path, monkeypatch):
    # The prompt asks the agent to report a blocker itself, but a run against a
    # push whose job treeherder-cli could not reach produced a full verdict
    # instead. The log check is what makes it a guarantee: no log, no verdict,
    # and the error reaches the API and the UI (bug 6665).
    repo = tmp_path / "src"
    repo.mkdir()
    _git_repo(repo)
    scratch_out = tmp_path / "out"
    scratch_out.mkdir()

    async def fake_session(reporter, options, prompt):
        # a confident verdict, but nothing was ever fetched into scratch_out/logs
        (scratch_out / "verdict.json").write_text(
            json.dumps({"recommendation": "backout", "culprit_commit": "HEAD"})
        )
        (scratch_out / "summary.md").write_text("the verdict")
        (scratch_out / "analysis.md").write_text("the reasoning")
        return _result_msg()

    monkeypatch.setattr(agent, "_run_session", fake_session)

    with pytest.raises(AgentError) as excinfo:
        asyncio.run(
            agent.run_test_repair(
                bugzilla_mcp_server=None,
                source_repo=repo,
                investigation=_investigation("headsha"),
                scratch_out=scratch_out,
                verbose=False,
                log=None,
            )
        )
    assert "no failure log was retrieved" in str(excinfo.value)


def test_the_culprits_bug_is_read_off_its_subject(tmp_path):
    repo = tmp_path / "src"
    repo.mkdir()
    _git_repo(repo)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-q", "--allow-empty", "-m", "Bug 555 - x"],
        check=True,
    )
    head = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    out = tmp_path / "out"
    out.mkdir()
    result = agent._assemble_result(
        out,
        verdict={"culprit_commit": head, "culprit_bug": 999},
        source_repo=repo,
        total_turns=1,
        total_cost=0.0,
        publish_file=None,
    )
    assert result.culprit_bug == 555


def test_the_analysis_is_scoped_to_the_sheriffs_decision(tmp_path, monkeypatch):
    _result, calls, _head = _run(tmp_path, [{"culprit_commit": None}], monkeypatch)
    prompt = _flat(calls[0])
    assert "Fixing the regression is not your job" in prompt
    assert "culprit_bug" not in prompt
    assert "Search for the bug the culprit landed under" not in prompt
    assert "a sheriff reads to trust the recommendation" in prompt
