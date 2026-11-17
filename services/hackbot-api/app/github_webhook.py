"""GitHub webhook payload handling: ``@hackbot`` mentions on pull requests.

Everything a run targets (installation, repository, pull request) is read from
the signed delivery, never from comment text. The route in
``app/routers/webhooks.py`` orchestrates detection, authorization and
``trigger_run``.
"""

import asyncio
import re
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any
from xml.sax.saxutils import escape, quoteattr

from app.github import GitHubApp

# The one action per event that carries a new comment. Edits and deletions are
# ignored, as are mentions in PR descriptions and plain issues.
TRIGGER_ACTIONS = MappingProxyType(
    {
        # A comment on the PR's conversation tab (PRs are issues to this API).
        "issue_comment": "created",
        # An inline comment on the diff, including replies in a thread.
        "pull_request_review_comment": "created",
        # A submitted review's top-level body.
        "pull_request_review": "submitted",
    }
)

PULL_REQUEST_READ_PERMISSIONS = MappingProxyType({"pull_requests": "read"})

# GitHub abandons a delivery that isn't answered within 10 seconds. Everything
# after the signature check (authorizing the sender, looking up the PR head and
# triggering the run) shares this budget, leaving margin to respond.
DELIVERY_DEADLINE_SECONDS = 8.0
# Within it, the authorizer's lookup is bounded at 5 seconds and this one at 3,
# so neither alone can use up the time meant for triggering the run.
HEAD_LOOKUP_TIMEOUT_SECONDS = 3.0


@dataclass(frozen=True)
class GitHubMention:
    """An actionable mention, resolved from the payload and ready to authorize."""

    installation_id: int
    owner: str
    repo: str
    pr_number: int
    # The payload's ``sender``, for the authorizer to vet (bots included). A
    # missing or null one is passed through and denied there.
    sender: Mapping[str, Any] | None
    # The triggering comment, rendered as a service-generated XML element.
    comment: str
    # Identifies the comment or review across redeliveries.
    dedupe_key: str
    # Review payloads carry the PR head; a conversation comment's doesn't, so
    # it is looked up with :func:`resolve_head_sha` instead.
    head_sha: str | None

    @property
    def full_repo_name(self) -> str:
        return f"{self.owner}/{self.repo}"

    @property
    def sender_login(self) -> str | None:
        """For logging: safe on a sender the authorizer will deny."""
        if not self.sender:
            # GitHub sends one with every event. Without it the authorizer
            # denies the mention, and the route logs that.
            return None
        return self.sender.get("login")


def _strip_quoted_lines(text: str) -> str:
    """Return ``text`` without its quoted (``>``) lines.

    As for Phabricator, so a mention copied from an earlier comment doesn't
    start a new run. Leading spaces are allowed, as GitHub's Markdown does.
    Mentions in code still count: they're rare, and only an authorized user
    can trigger a run anyway.
    """
    return "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith(">")
    )


def mentions(body: str | None, token: str) -> bool:
    """Whether ``body`` mentions ``token`` as a whole handle, case-insensitively.

    ``@hackbot-dev`` and ``me@hackbot`` are not mentions of ``@hackbot``.
    """
    if not body or not token:
        return False
    pattern = rf"(?<![\w-]){re.escape(token)}(?![\w-])"
    return re.search(pattern, _strip_quoted_lines(body), re.IGNORECASE) is not None


def _format_comment(body: str, attributes: Mapping[str, object]) -> str:
    """Render the triggering comment as a service-generated XML element.

    For example, an inline comment is rendered as::

        <comment comment_id="102" type="inline" path="dom/base/Foo.cpp" line="42">
          @hackbot fix this
        </comment>
    """
    rendered = " ".join(
        f"{name}={quoteattr(str(value))}"
        for name, value in attributes.items()
        if value is not None
    )
    text = "\n".join(f"    {line}" for line in escape(body).splitlines())
    return f"  <comment {rendered}>\n{text}\n  </comment>"


def detect_mention(
    event: str,
    payload: Mapping[str, Any],
    *,
    mention_token: str,
) -> GitHubMention | None:
    """Return the :class:`GitHubMention` in this delivery, or ``None``.

    Only new comments on open pull requests count. Who sent them, including
    whether it was a bot, is left to the authorizer.
    """
    if event not in TRIGGER_ACTIONS or payload.get("action") != TRIGGER_ACTIONS[event]:
        return None

    head_sha = None
    if event == "issue_comment":
        issue = payload["issue"]
        if not issue.get("pull_request") or issue["state"] != "open":
            return None
        pr_number = issue["number"]
        item = payload["comment"]
        attributes = {"comment_id": item["id"], "type": "regular"}
        dedupe_key = f"github-issue-comment:{item['id']}"
    else:
        pull_request = payload["pull_request"]
        if pull_request["state"] != "open":
            return None
        pr_number = pull_request["number"]
        head_sha = pull_request["head"]["sha"]
        if event == "pull_request_review_comment":
            item = payload["comment"]
            attributes = {
                "comment_id": item["id"],
                "type": "inline",
                "path": item.get("path"),
                "line": item.get("line") or item.get("original_line"),
                "commit_id": item.get("commit_id"),
            }
            dedupe_key = f"github-review-comment:{item['id']}"
        else:
            item = payload["review"]
            attributes = {"review_id": item["id"], "type": "review"}
            dedupe_key = f"github-review:{item['id']}"

    body = item.get("body")
    if not mentions(body, mention_token):
        return None

    repository = payload["repository"]
    return GitHubMention(
        installation_id=payload["installation"]["id"],
        owner=repository["owner"]["login"],
        repo=repository["name"],
        pr_number=pr_number,
        sender=payload.get("sender"),
        comment=_format_comment(body, attributes),
        dedupe_key=dedupe_key,
        head_sha=head_sha,
    )


async def resolve_head_sha(
    github_app: GitHubApp,
    mention: GitHubMention,
    *,
    timeout_seconds: float = HEAD_LOOKUP_TIMEOUT_SECONDS,
) -> str | None:
    """The PR's current head SHA, or ``None`` if the PR is no longer open.

    Uses the installation the event came from, with a read-only token narrowed
    to that one repository. Raises :class:`TimeoutError` if minting the token
    and fetching the PR together outlast ``timeout_seconds``.
    """
    github = github_app.as_installation(
        mention.installation_id,
        permissions=PULL_REQUEST_READ_PERMISSIONS,
        repositories=[mention.repo],
    )
    async with asyncio.timeout(timeout_seconds):
        response = await github.rest.pulls.async_get(
            mention.owner, mention.repo, mention.pr_number
        )
    # Two fields from the raw body rather than ``parsed_data``, which validates
    # the whole PullRequest model and so breaks on unrelated schema drift.
    pull_request = response.json()
    if pull_request["state"] != "open":
        return None
    return pull_request["head"]["sha"]
