import asyncio
import json
import subprocess
from types import SimpleNamespace

from hackbot_agents.test_repair import agent
from hackbot_agents.test_repair.config import BUILD_TOOL, SKIP_FIREFOX_BUILD
from hackbot_agents.test_repair.prompts import MAX_TESTS_PER_GROUP
from hackbot_agents.test_repair.resolve import (
    CommitRange,
    FailingGroup,
    Investigation,
)


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
    base,
    complete=True,
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
        last_green_revision="greenhg",
        commit_range=CommitRange(head=head, base=base, span=2, complete=complete),
        label=label,
        group_based=group_based,
        known_intermittent_bugs=known_intermittent_bugs or [],
    )


def _fx_ctx(tmp_path):
    return SimpleNamespace(
        source_dir=tmp_path / "src",
        mozconfig=tmp_path / ".mozconfig",
        objdir=tmp_path / "obj",
    )


def _run(
    tmp_path,
    verdicts,
    monkeypatch,
    complete=True,
    results=None,
    platform="linux1804-64/opt",
    groups=None,
    group_based=True,
    known_intermittent_bugs=None,
    skip_firefox_build=SKIP_FIREFOX_BUILD,
    options_out=None,
):
    repo = tmp_path / "src"
    repo.mkdir()
    base, head = _git_repo(repo)
    scratch_out = tmp_path / "out"
    scratch_out.mkdir()
    calls = []

    async def fake_session(reporter, options, prompt):
        calls.append(prompt)
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
        return _result_msg(is_error=bool(results and results.pop(0)))

    bootstrapped = []

    async def fake_bootstrap(firefox_dir):
        bootstrapped.append(firefox_dir)
        return {"success": True}

    monkeypatch.setattr(agent, "_run_session", fake_session)
    monkeypatch.setattr(agent, "bootstrap_firefox", fake_bootstrap)
    monkeypatch.setattr(agent, "build_sdk_server", lambda *a, **k: {"type": "sdk"})

    result = asyncio.run(
        agent.run_test_repair(
            bugzilla_mcp_server=None,
            source_repo=repo,
            fx_ctx=_fx_ctx(tmp_path),
            investigation=_investigation(
                head,
                base if complete else None,
                complete,
                platform,
                groups,
                group_based,
                known_intermittent_bugs=known_intermittent_bugs,
            ),
            task_logs={},
            scratch_out=scratch_out,
            skip_firefox_build=skip_firefox_build,
            verbose=False,
            log=None,
        )
    )
    return result, calls, head


def test_culprit_runs_fix_stage(tmp_path, monkeypatch):
    result, calls, head = _run(
        tmp_path,
        [
            {"recommendation": "backout", "culprit_commit": "HEAD", "confidence": 0.9},
            {
                "recommendation": "backout",
                "culprit_commit": "HEAD",
                "confidence": 0.9,
                "proposed_patch": True,
            },
        ],
        monkeypatch,
    )
    assert len(calls) == 2  # analysis + fix
    assert result.classification == "regression"
    assert result.culprit_commit == head
    assert result.recommendation == "backout"
    assert result.proposed_patch is True
    assert result.last_green_revision == "greenhg"
    assert result.num_turns == 6


def test_no_culprit_skips_fix_stage(tmp_path, monkeypatch):
    result, calls, _head = _run(
        tmp_path,
        [{"recommendation": "backout", "culprit_commit": None, "confidence": 0.3}],
        monkeypatch,
    )
    assert len(calls) == 1  # no fix stage
    assert result.culprit_commit is None
    assert result.proposed_patch is False
    assert result.recommendation == "do_not_backout"


def test_hallucinated_culprit_is_discarded(tmp_path, monkeypatch):
    result, calls, _head = _run(
        tmp_path,
        [{"recommendation": "backout", "culprit_commit": "deadbeefdeadbeef"}],
        monkeypatch,
    )
    assert len(calls) == 1
    assert result.culprit_commit is None
    assert result.recommendation == "do_not_backout"


def test_fix_stage_verdict_rewrite_keeps_analysis_culprit(tmp_path, monkeypatch):
    # The fix stage may rewrite the whole file; the culprit must survive that.
    result, _calls, head = _run(
        tmp_path,
        [
            {
                "recommendation": "backout",
                "culprit_commit": "HEAD",
                "culprit_bug": 123,
                "confidence": 0.9,
            },
            {"recommendation": "backout", "proposed_patch": True},
        ],
        monkeypatch,
    )
    assert result.culprit_commit == head
    assert result.culprit_bug == 123
    assert result.confidence == 0.9
    assert result.recommendation == "backout"


def test_failed_fix_stage_still_publishes_analysis(tmp_path, monkeypatch):
    result, calls, head = _run(
        tmp_path,
        [
            {"recommendation": "backout", "culprit_commit": "HEAD", "confidence": 0.9},
            None,
        ],
        monkeypatch,
        results=[False, True],
    )
    assert len(calls) == 2
    assert result.culprit_commit == head
    assert result.recommendation == "backout"
    assert result.analysis == "the reasoning"


def test_complete_range_prompt_gives_a_base_anchored_range(tmp_path, monkeypatch):
    _result, calls, head = _run(tmp_path, [{"culprit_commit": None}], monkeypatch)
    assert "culprit is one of them" in calls[0]
    # The agent enumerates the range itself rather than being handed every sha.
    assert "git log --oneline" in calls[0]
    assert f"..{head}" in calls[0]


def test_incomplete_range_prompt_does_not_assert_the_culprit(tmp_path, monkeypatch):
    _result, calls, _head = _run(
        tmp_path, [{"culprit_commit": None}], monkeypatch, complete=False
    )
    assert "may predate" in calls[0]
    assert "culprit is one of them" not in calls[0]
    # Without a last-green base the range falls back to the clone depth.
    assert "HEAD~2..HEAD" in calls[0]


def test_assemble_defaults_on_empty_verdict(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    result = agent._assemble_result(
        out,
        verdict={},
        source_repo=tmp_path,
        last_green_revision=None,
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
            "culprit_bug": "n/a",
        },
        source_repo=tmp_path,
        last_green_revision=None,
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
        last_green_revision=None,
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
    assert agent._coerce_recommendation("nonsense", "regression", True) == "backout"
    # "backout" is meaningless without a commit to back out.
    assert agent._coerce_recommendation("backout", "regression", False) == (
        "do_not_backout"
    )


def test_resolve_culprit_normalizes_against_the_checkout(tmp_path):
    repo = tmp_path / "src"
    repo.mkdir()
    _base, head = _git_repo(repo)
    assert agent._resolve_culprit(repo, head) == head
    assert agent._resolve_culprit(repo, head[:8]) == head
    assert agent._resolve_culprit(repo, "deadbeefdeadbeefdeadbeef") is None
    assert agent._resolve_culprit(repo, None) is None
    assert agent._resolve_culprit(repo, "  ") is None


def test_range_expr_prefers_the_last_green_base():
    anchored = CommitRange(head="h" * 40, base="b" * 40, span=5, complete=True)
    assert agent._range_expr(anchored) == f"{'b' * 40}..{'h' * 40}"
    assert agent._range_expr(CommitRange("h" * 40, None, 5, False)) == "HEAD~5..HEAD"


def test_both_stages_name_the_checkout_path(tmp_path, monkeypatch):
    # Only the scratch dir used to be named, so the agent cd'd there and then had
    # to hunt the filesystem for the tree -- in both stages.
    _result, calls, _head = _run(
        tmp_path,
        [{"culprit_commit": "HEAD"}, {"proposed_patch": True}],
        monkeypatch,
    )
    repo = str(tmp_path / "src")
    assert len(calls) == 2
    for prompt in calls:
        assert repo in prompt


def test_both_stages_steer_tree_searches_to_git_grep(tmp_path, monkeypatch):
    # An unbounded `grep -r` over the Firefox tree burns the whole Bash timeout.
    _result, calls, _head = _run(
        tmp_path,
        [{"culprit_commit": "HEAD"}, {"proposed_patch": True}],
        monkeypatch,
    )
    assert len(calls) == 2
    for prompt in calls:
        assert "`git grep`" in prompt


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
    assert "it does not clear anyone" in calls[0]
    assert "go through the rest of the list" in calls[0]


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


def test_linux_failure_expects_the_test_to_pass(tmp_path, monkeypatch):
    _result, calls, _head = _run(
        tmp_path,
        [{"culprit_commit": "HEAD"}, {"proposed_patch": True}],
        monkeypatch,
        skip_firefox_build=False,
    )
    assert "mach" in calls[1]
    assert "They should pass." in calls[1]
    assert "proves nothing" not in calls[1]


def test_non_linux_failure_still_runs_the_test_but_discounts_a_pass(
    tmp_path, monkeypatch
):
    # A failure on Linux is real evidence the patch is wrong; a pass says nothing
    # about the failing platform, so it must not count as verification.
    _result, calls, _head = _run(
        tmp_path,
        [{"culprit_commit": "HEAD"}, {"proposed_patch": True}],
        monkeypatch,
        platform="windows11-64-24h2/opt",
        skip_firefox_build=False,
    )
    assert "mach" in calls[1]
    assert "proves nothing about windows11-64-24h2/opt" in calls[1]
    assert "not verified" in calls[1]


def _mozconfig_for(tmp_path, platform):
    tmp_path.mkdir(parents=True, exist_ok=True)
    fx = _fx_ctx(tmp_path)
    agent._write_mozconfig(fx, _investigation("h", None, platform=platform))
    return fx.mozconfig.read_text()


def test_mozconfig_mirrors_the_ci_build_variant(tmp_path):
    opt = _mozconfig_for(tmp_path / "opt", "linux1804-64-qr/opt")
    assert "--disable-debug" in opt and "--enable-optimize" in opt
    assert "sanitizer" not in opt

    dbg = _mozconfig_for(tmp_path / "dbg", "linux1804-64-qr/debug")
    assert "--enable-debug" in dbg

    # A plain build cannot trigger a sanitizer or coverage failure at all.
    asan = _mozconfig_for(tmp_path / "asan", "linux1804-64-asan-qr/opt")
    assert "--enable-address-sanitizer" in asan and "--disable-jemalloc" in asan

    tsan = _mozconfig_for(tmp_path / "tsan", "linux1804-64-tsan-qr/opt")
    assert "--enable-thread-sanitizer" in tsan

    ccov = _mozconfig_for(tmp_path / "ccov", "linux1804-64-ccov/opt")
    assert "--enable-coverage" in ccov


def test_verify_step_states_the_container_limits(tmp_path, monkeypatch):
    _result, calls, _head = _run(
        tmp_path,
        [{"culprit_commit": "HEAD"}, {"proposed_patch": True}],
        monkeypatch,
        platform="linux1804-64-qr/debug",
        skip_firefox_build=False,
    )
    assert "virtual display" in calls[1]
    assert "could not verify" in calls[1]


def test_last_green_is_labelled_as_an_hg_revision(tmp_path, monkeypatch):
    # Every other revision in the prompt is a git hash; an unlabelled hg node
    # makes the agent run git commands against it and get exit 128.
    _result, calls, _head = _run(tmp_path, [{"culprit_commit": None}], monkeypatch)
    assert "hg revision greenhg" in calls[0]
    assert "not a git object" in calls[0]


def test_mozconfig_overwrites_a_foreign_one(tmp_path):
    # /workspace is shared with the other agents, so a leftover mozconfig points
    # the build at a foreign objdir with foreign flags.
    tmp_path.mkdir(parents=True, exist_ok=True)
    fx = _fx_ctx(tmp_path)
    fx.mozconfig.write_text(
        "ac_add_options --enable-release\n"
        "mk_add_options MOZ_OBJDIR=/workspace/firefox/objdir-build-repair\n"
    )
    agent._write_mozconfig(fx, _investigation("h", None, platform="linux1804-64/opt"))
    written = fx.mozconfig.read_text()
    assert "objdir-build-repair" not in written
    assert "--enable-release" not in written
    assert str(fx.objdir) in written


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


def test_analysis_prompt_frames_the_recommendation_as_the_sheriff_action(
    tmp_path, monkeypatch
):
    _result, calls, _head = _run(tmp_path, [{"culprit_commit": None}], monkeypatch)
    assert '"backout", "do_not_backout" or "rerun"' in _flat(calls[0])
    assert "land_fix" not in calls[0]
    assert "sheriff's action" in calls[0]


def test_fix_prompt_keeps_the_backout_and_asks_for_a_squashed_reland(
    tmp_path, monkeypatch
):
    _result, calls, _head = _run(
        tmp_path,
        [{"culprit_commit": "HEAD"}, {"proposed_patch": True}],
        monkeypatch,
    )
    assert "squash" in calls[1]
    assert "not a follow-up" in _flat(calls[1])
    assert 'leave "recommendation" as "backout"' in _flat(calls[1])


def test_analysis_prompt_asks_for_the_whole_stack(tmp_path, monkeypatch):
    # A culprit at the bottom of a stack cannot be backed out on its own; the
    # commits above it were written against the broken change.
    _result, calls, _head = _run(tmp_path, [{"culprit_commit": None}], monkeypatch)
    assert "`Bug NNNNNN`" in calls[0]
    assert "whole stack has to be backed out" in _flat(calls[0])


def test_analysis_prompt_rules_out_follow_ups(tmp_path, monkeypatch):
    _result, calls, _head = _run(tmp_path, [{"culprit_commit": None}], monkeypatch)
    assert "Never suggest a follow-up patch" in _flat(calls[0])
    assert "land in one push" in _flat(calls[0])


def test_skip_firefox_build_still_patches_but_never_builds(tmp_path, monkeypatch):
    # The fix stage is still worth running without a build: the patch is developer
    # advice for the reland, and it costs nothing to compile it here.
    options = []
    result, calls, head = _run(
        tmp_path,
        [
            {"recommendation": "backout", "culprit_commit": "HEAD", "confidence": 0.5},
            {
                "recommendation": "backout",
                "culprit_commit": "HEAD",
                "confidence": 0.5,
                "proposed_patch": True,
            },
        ],
        monkeypatch,
        skip_firefox_build=True,
        options_out=options,
    )
    assert len(calls) == 2  # analysis + fix
    assert result.culprit_commit == head
    assert result.proposed_patch is True
    # Nothing that leads to a build may have run.
    assert not (tmp_path / ".mozconfig").exists()
    # The build tool is not merely discouraged, it is not on offer.
    for opts in options:
        assert BUILD_TOOL not in opts.allowed_tools
        assert "firefox" not in opts.mcp_servers
    assert "Do not build" in calls[1]
    assert "unverified" in calls[1]


def test_not_building_is_the_default(tmp_path, monkeypatch):
    # Pins config.SKIP_FIREFOX_BUILD: nothing is passed here, so a change to the
    # default flips this test rather than silently changing every run.
    assert SKIP_FIREFOX_BUILD is True
    options = []
    _result, calls, _head = _run(
        tmp_path,
        [
            {"recommendation": "backout", "culprit_commit": "HEAD", "confidence": 0.5},
            {"recommendation": "backout", "culprit_commit": "HEAD", "confidence": 0.5},
        ],
        monkeypatch,
        options_out=options,
    )
    assert not (tmp_path / ".mozconfig").exists()
    assert BUILD_TOOL not in options[0].allowed_tools
    assert "Do not build" in calls[1]


def test_opting_into_the_build_verifies_the_fix(tmp_path, monkeypatch):
    options = []
    _result, calls, _head = _run(
        tmp_path,
        [
            {"recommendation": "backout", "culprit_commit": "HEAD", "confidence": 0.5},
            {"recommendation": "backout", "culprit_commit": "HEAD", "confidence": 0.5},
        ],
        monkeypatch,
        skip_firefox_build=False,
        options_out=options,
    )
    assert (tmp_path / ".mozconfig").exists()
    assert BUILD_TOOL in options[0].allowed_tools
    assert "build_firefox tool" in calls[1]
    assert "Do not build" not in calls[1]
