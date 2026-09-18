"""Tests for the Phabricator diff fetch, the one filesystem side effect of setup.

Conduit is stubbed and the filesystem is real: the point is which diff is
asked for and where it lands, not HTTP.
"""

from __future__ import annotations

import pytest
from hackbot_agents.uplift_merge_conflict_resolver.agent import (
    build_phabricator_client,
    describe_requested,
    fetch_source_diffs,
)
from hackbot_agents.uplift_merge_conflict_resolver.config import (
    GitSource,
    PhabricatorSource,
)
from hackbot_runtime import AgentError
from phabricator_client import UnresolvedCommitError


class StubConduit:
    """A `PhabricatorClient` stand-in over a fake `differential.querydiffs`.

    Stubbed at the client level; `phabricator-client` already tests the call
    against real envelopes. The envelope *shape* is faithful, though -- keyed
    by stringified id, with `id` a string too -- which is what makes the
    `int()` coercion in `resolve_diff` load-bearing. ``diffs`` maps a revision
    id to the diff ids it has, newest last.
    """

    def __init__(
        self,
        diffs: dict[int, list[int]] | None = None,
        without_author: set[int] | None = None,
        unresolvable: set[str] | None = None,
    ) -> None:
        self.diffs = diffs or {}
        self.without_author = without_author or set()
        self.unresolvable = unresolvable or set()
        self.raw_diff_calls: list[int] = []
        self.resolve_calls: list[str] = []

    async def conduit_request(self, method: str, **payload):
        assert method == "differential.querydiffs", (
            "The agent should resolve diffs through `differential.querydiffs`."
        )
        (revision_id,) = payload["revisionIDs"]
        return {
            str(diff_id): self._raw(diff_id)
            for diff_id in self.diffs.get(revision_id, [])
        }

    def _raw(self, diff_id: int) -> dict:
        raw: dict = {
            "id": str(diff_id),
            "sourceControlBaseRevision": f"base{diff_id}",
        }
        if diff_id not in self.without_author:
            raw["authorName"] = f"Author {diff_id}"
            raw["authorEmail"] = f"author{diff_id}@example.com"
        return raw

    async def resolve_commit(self, ref: str) -> str:
        """Expand an abbreviated base hash, as `diffusion.querycommits` does."""
        self.resolve_calls.append(ref)
        if ref in self.unresolvable:
            raise UnresolvedCommitError(f"cannot resolve {ref}")
        return ref.ljust(40, "0")

    async def get_raw_diff(self, diff_id: int) -> str:
        self.raw_diff_calls.append(diff_id)
        return f"diff for {diff_id}"


async def test_writes_fetched_diff_to_expected_path(tmp_path):
    client = StubConduit(diffs={9: [400]})
    source = PhabricatorSource(revision_id=9)

    fetched = await fetch_source_diffs(client, [source], tmp_path)

    expected = source.diff_path(tmp_path, 400)
    assert [entry.path for entry in fetched] == [expected], (
        "The written path should match the source's own `diff_path`."
    )
    assert expected.read_text() == "diff for 400", (
        "The file should hold the diff Conduit returned."
    )


async def test_two_pins_on_one_revision_do_not_share_a_file(tmp_path):
    """Every source is fetched up front, so a shared filename loses a pin."""
    client = StubConduit(diffs={9: [10, 20]})
    sources = [
        PhabricatorSource(revision_id=9, diff_id=10),
        PhabricatorSource(revision_id=9, diff_id=20),
    ]

    fetched = await fetch_source_diffs(client, sources, tmp_path)

    assert [entry.path.read_text() for entry in fetched] == [
        "diff for 10",
        "diff for 20",
    ], "Each source must read the diff it pinned, not whichever was written last."


async def test_pinned_diff_id_is_used_verbatim(tmp_path):
    client = StubConduit(diffs={9: [317, 400]})

    await fetch_source_diffs(
        client, [PhabricatorSource(revision_id=9, diff_id=317)], tmp_path
    )

    assert client.raw_diff_calls == [317], (
        "A pinned `diff_id` should be fetched instead of the revision's latest."
    )


async def test_missing_diff_id_falls_back_to_the_latest():
    client = StubConduit(diffs={9: [317, 400]})

    diff = await PhabricatorSource(revision_id=9).resolve_diff(client)

    assert diff.id == 400, "Without a pin, the revision's newest diff is used."


async def test_revision_without_any_diff_is_an_agent_error():
    client = StubConduit(diffs={})

    with pytest.raises(AgentError, match="D9 has no diffs"):
        await PhabricatorSource(revision_id=9).resolve_diff(client)


async def test_pinned_diff_from_another_revision_is_an_agent_error():
    client = StubConduit(diffs={9: [400]})

    with pytest.raises(AgentError, match="does not belong to D9"):
        await PhabricatorSource(revision_id=9, diff_id=123).resolve_diff(client)


async def test_author_travels_with_the_diff(tmp_path):
    client = StubConduit(diffs={9: [400]})

    fetched = await fetch_source_diffs(
        client, [PhabricatorSource(revision_id=9)], tmp_path
    )

    assert fetched[0].author == "Author 400 <author400@example.com>", (
        "The uplift commit needs the original author, as `Name <email>`."
    )


async def test_base_commit_is_expanded_to_a_full_hash(tmp_path):
    client = StubConduit(diffs={9: [400]})

    fetched = await fetch_source_diffs(
        client, [PhabricatorSource(revision_id=9)], tmp_path
    )

    assert client.resolve_calls == ["base400"], (
        "The recorded base should be expanded through Conduit before being used."
    )
    assert fetched[0].base_commit == "base400".ljust(40, "0"), (
        "moz-phab records an abbreviated base and `git fetch` refuses one, so "
        "the full hash is what reaches the prompt."
    )


async def test_an_unexpandable_base_commit_is_reported_as_none(tmp_path):
    client = StubConduit(diffs={9: [400]}, unresolvable={"base400"})

    fetched = await fetch_source_diffs(
        client, [PhabricatorSource(revision_id=9)], tmp_path
    )

    assert fetched[0].base_commit is None, (
        "A base that cannot be expanded should send the agent down the prompt's "
        "fallback, not fail the run over a three-way merge hint."
    )


async def test_requested_sources_record_what_each_input_resolved_to(tmp_path):
    """An unpinned input does not say which diff ran, so the result records it."""
    client = StubConduit(diffs={9: [317, 400]})
    sources = [GitSource(commit="abc"), PhabricatorSource(revision_id=9)]

    requested = describe_requested(
        sources, await fetch_source_diffs(client, sources, tmp_path)
    )

    assert [entry.source for entry in requested] == [
        {"kind": "git", "commit": "abc"},
        {"kind": "phabricator", "revision_id": 9, "diff_id": None},
    ], "Each source should be recorded as the caller gave it."
    assert requested[1].diff_id == 400, (
        "The diff actually fetched should be recorded, not the empty pin."
    )
    assert requested[1].base_commit == "base400".ljust(40, "0"), (
        "The expanded base commit belongs in the record a reviewer reads."
    )
    assert requested[1].author == "Author 400 <author400@example.com>", (
        "Who the uplift is attributed to should be recorded too."
    )
    assert (requested[0].diff_id, requested[0].base_commit) == (None, None), (
        "A git source has no diff to resolve; it is cherry-picked as-is."
    )


async def test_git_sources_fetch_nothing(tmp_path):
    client = StubConduit()

    fetched = await fetch_source_diffs(client, [GitSource(commit="abc")], tmp_path)

    assert fetched == [None], (
        "A git source is cherry-picked, so it holds a slot but fetches nothing."
    )
    assert client.raw_diff_calls == [], "A git source should not reach Conduit."


async def test_a_stack_is_fetched_in_order(tmp_path):
    client = StubConduit(diffs={1: [10], 2: [88, 99]})
    sources = [
        GitSource(commit="aaa"),
        PhabricatorSource(revision_id=1),
        PhabricatorSource(revision_id=2, diff_id=88),
    ]

    fetched = await fetch_source_diffs(client, sources, tmp_path)

    assert [entry is None for entry in fetched] == [True, False, False], (
        "The result is positional, so the git source keeps its slot."
    )
    assert client.raw_diff_calls == [10, 88], (
        "Each revision resolves to its own diff, pinned or latest, in stack order."
    )


def test_client_points_at_the_brokers_proxy_mount():
    client = build_phabricator_client("http://uplift-broker:8765/")

    assert client.base_url == "http://uplift-broker:8765/phabricator", (
        "The client should target the broker's proxy mount, with no double slash."
    )
