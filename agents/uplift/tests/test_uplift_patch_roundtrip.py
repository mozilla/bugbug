"""End-to-end check that a resolved uplift survives being turned into a patch.

Everything downstream consumes `changes.patch`, not the checkout, so the run is
only useful if that patch reapplies to a clean target, one commit per source,
with each commit's own metadata intact. Driven against real repositories: a
genuine conflict, a committed resolution, then `collect` and `git am` onto a
fresh clone.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from hackbot_agents.uplift_merge_conflict_resolver import agent
from hackbot_agents.uplift_merge_conflict_resolver.models import GitSource
from hackbot_runtime import changes

AUTHOR = "Original Author <original@example.com>"
REPO_URL = "https://example.invalid/firefox.git"

# What the patch does on trunk, what the stable branch did instead, and the
# resolution that has to keep the patch's change while fitting the branch.
ON_TRUNK = "line1\nline2 patched\nline3\n"
ON_STABLE = "line1\nline2 diverged\nline3\nline4\n"
RESOLVED = "line1\nline2 patched\nline3\nline4\n"


def build_upstream(git_in, path: Path) -> str:
    """A remote with a patch on trunk and a diverged stable branch.

    Returns the commit to uplift.
    """
    path.mkdir()
    git_in(path, "init", "-q")
    git_in(path, "config", "user.email", "trunk@example.com")
    git_in(path, "config", "user.name", "Trunk Dev")
    (path / "f.txt").write_text("line1\nline2\nline3\n")
    git_in(path, "add", "-A")
    git_in(path, "commit", "-qm", "base")
    base = git_in(path, "rev-parse", "HEAD")

    (path / "f.txt").write_text(ON_TRUNK)
    git_in(path, "add", "-A")
    git_in(path, "commit", "-qm", "Bug 1: patch to uplift", f"--author={AUTHOR}")
    to_uplift = git_in(path, "rev-parse", "HEAD")

    git_in(path, "checkout", "-q", "-b", "stable", base)
    (path / "f.txt").write_text(ON_STABLE)
    git_in(path, "add", "-A")
    git_in(path, "commit", "-qm", "unrelated change on stable")
    git_in(path, "checkout", "-q", "stable")
    return to_uplift


def clone_stable(git_in, upstream: Path, path: Path) -> Path:
    """A checkout of the stable branch, as the runtime prepares one."""
    git_in(path.parent, "clone", "-q", "--branch", "stable", str(upstream), path.name)
    git_in(path, "config", "user.email", "agent@example.com")
    git_in(path, "config", "user.name", "Hackbot Agent")
    return path


def test_a_resolved_uplift_reapplies_to_a_clean_target(tmp_path, git_in):
    upstream = tmp_path / "upstream"
    to_uplift = build_upstream(git_in, upstream)
    checkout = clone_stable(git_in, upstream, tmp_path / "checkout")
    base = git_in(checkout, "rev-parse", "HEAD")

    # The conflict the agent is spun up to deal with.
    git_in(checkout, "fetch", "-q", "origin", to_uplift)
    git_in(checkout, "cherry-pick", "-x", to_uplift, check=False)
    assert git_in(checkout, "diff", "--name-only", "--diff-filter=U") == "f.txt", (
        "The setup should leave a real conflict, or this proves nothing."
    )

    # The session's work: resolve, then finish the pick.
    (checkout / "f.txt").write_text(RESOLVED)
    git_in(checkout, "add", "-A")
    git_in(checkout, "-c", "core.editor=true", "cherry-pick", "--continue")

    change_set = changes.collect(checkout, base, REPO_URL)
    assert change_set is not None, "A resolved uplift should collect to a patch."
    assert change_set.metadata["wrapped_uncommitted"] is False, (
        "The resolution was committed, so nothing should need wrapping into a "
        "synthetic commit owned by the container."
    )

    # What a reviewer, or Lando, does with the artifact.
    target = clone_stable(git_in, upstream, tmp_path / "target")
    mbox = tmp_path / "changes.patch"
    mbox.write_bytes(change_set.patch)
    git_in(target, "am", str(mbox))

    assert (target / "f.txt").read_text() == RESOLVED, (
        "Reapplying the collected patch should reproduce the resolution."
    )
    assert git_in(target, "log", "-1", "--format=%an <%ae>") == AUTHOR, (
        "A cherry-picked commit carries its author, and the mbox should keep it."
    )


def test_an_uncommitted_resolution_is_flagged_as_wrapped(tmp_path, git_in):
    """What the prompt warns about: uncommitted work is squashed into one commit."""
    upstream = tmp_path / "upstream"
    to_uplift = build_upstream(git_in, upstream)
    checkout = clone_stable(git_in, upstream, tmp_path / "checkout")
    base = git_in(checkout, "rev-parse", "HEAD")

    git_in(checkout, "fetch", "-q", "origin", to_uplift)
    git_in(checkout, "cherry-pick", "-x", to_uplift, check=False)
    (checkout / "f.txt").write_text(RESOLVED)

    change_set = changes.collect(checkout, base, REPO_URL)

    assert change_set is not None, "There is still work to collect."
    assert change_set.metadata["wrapped_uncommitted"] is True, (
        "Left uncommitted, the resolution is swept into one synthetic commit -- "
        "which is why the run refuses to call this resolved."
    )


async def test_a_run_uplift_resolution_reapplies(
    tmp_path, git_in, monkeypatch, publisher
):
    """The whole path in one go: run the agent, then land what it produced.

    The stand-in session does what the prompt asks for real -- fetch, pick,
    resolve, finish the pick -- so the run's own checks, the base commit it
    records, and the patch collected from it are all exercised together.
    """
    upstream = tmp_path / "upstream"
    to_uplift = build_upstream(git_in, upstream)
    checkout = clone_stable(git_in, upstream, tmp_path / "checkout")

    async def session(reporter, options, prompt):
        repo = Path(options.cwd)
        git_in(repo, "fetch", "-q", "origin", to_uplift)
        git_in(repo, "cherry-pick", "-x", to_uplift, check=False)
        assert git_in(repo, "diff", "--name-only", "--diff-filter=U") == "f.txt", (
            "The uplift should conflict, or the run has nothing to resolve."
        )
        (repo / "f.txt").write_text(RESOLVED)
        git_in(repo, "add", "-A")
        git_in(repo, "-c", "core.editor=true", "cherry-pick", "--continue")
        Path(options.add_dirs[0], "report.json").write_text(
            json.dumps(
                {
                    "resolved": True,
                    "confidence": "high",
                    "summary": "Kept the patch's change alongside the branch's.",
                }
            )
        )
        return SimpleNamespace(
            is_error=False,
            result="done",
            subtype="success",
            num_turns=3,
            total_cost_usd=0.1,
        )

    monkeypatch.setattr(agent, "run_session", session)
    result = await agent.run_uplift(
        bugbug_mcp_server={"type": "http", "url": "http://localhost:8080/mcp"},
        broker_url="http://localhost:8765",
        source_repo=checkout,
        target_branch="stable",
        sources=[GitSource(commit=to_uplift)],
        publish_file=publisher,
    )

    assert (result.resolved, result.verification_failures) == (True, []), (
        "A real resolution, committed, should pass the checks."
    )
    assert json.loads(publisher.bodies["report.json"])["resolved"] is True, (
        "The published report should agree with it."
    )

    change_set = changes.collect(checkout, result.base_commit, REPO_URL)
    assert change_set is not None, (
        "The base commit the run recorded should be what the patch is collected "
        "against."
    )

    target = clone_stable(git_in, upstream, tmp_path / "target")
    mbox = tmp_path / "changes.patch"
    mbox.write_bytes(change_set.patch)
    git_in(target, "am", str(mbox))

    assert (target / "f.txt").read_text() == RESOLVED, (
        "The run's patch should reproduce its resolution on a clean target."
    )
    assert git_in(target, "log", "-1", "--format=%an <%ae>") == AUTHOR, (
        "And carry the picked commit's author through to the target."
    )
