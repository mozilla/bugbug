import json

from hackbot_agents.build_repair.agent import _push_context, _resolve_blame

TIP = "c929f8f0579c8d96c5df33067e38a424b17c77f0"
STACKED = "7b15e34863cf6b30b613ffadf9d6431fe5a55585"
EARLIER = "91b3a385edd8f0e3c2b1a09d8c7e6f5a4b3c2d1e"


def _blame(tmp_path, **content):
    (tmp_path / "blame.json").write_text(json.dumps(content))
    return tmp_path


def test_a_blamed_push_commit_is_kept_in_full(tmp_path):
    assert (
        _resolve_blame(_blame(tmp_path, blamed_commit=STACKED[:10]), [TIP, STACKED])
        == STACKED
    )


def test_an_explicit_null_clears_the_push(tmp_path):
    assert _resolve_blame(_blame(tmp_path, blamed_commit=None), [TIP, STACKED]) is None


def test_a_commit_from_an_earlier_push_is_kept_rather_than_the_checked_out_one(
    tmp_path,
):
    # Substituting the tip contradicted the agent's own analysis (bug 6788).
    assert _resolve_blame(_blame(tmp_path, blamed_commit=EARLIER), [TIP]) == EARLIER


def test_something_that_is_not_a_sha_blames_nobody(tmp_path):
    assert _resolve_blame(_blame(tmp_path, blamed_commit="unknown"), [TIP]) is None


def test_a_missing_verdict_falls_back_to_the_failure_commit(tmp_path):
    assert _resolve_blame(tmp_path, [TIP, STACKED]) == TIP


def test_an_unparsable_verdict_falls_back_to_the_failure_commit(tmp_path):
    (tmp_path / "blame.json").write_text("{not json")
    assert _resolve_blame(tmp_path, [TIP]) == TIP


def test_a_single_commit_push_is_spelled_out():
    context = _push_context([TIP])
    assert f"consists of commit {TIP} alone" in context
    assert "earlier pushes" in context


def test_a_multi_commit_push_lists_every_commit():
    context = _push_context([TIP, STACKED])
    assert f"- {TIP}\n- {STACKED}" in context
    assert "earlier pushes" in context
