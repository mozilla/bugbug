# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

"""Rule-based external content loading for code review.

Fetches review-context.toml from a GitHub repository, matches changed files
against the rules, and pre-loads the referenced content.
"""

import fnmatch
import hashlib
import json
import time
from dataclasses import dataclass
from logging import getLogger
from typing import Literal

import httpx
from pydantic import BaseModel
from unidiff import PatchSet

from bugbug.tools.code_review.data_types import (
    ExternalContent,
    ExternalContentLoadError,
    _fetch_url,
)
from bugbug.tools.code_review.review_context_schema import (
    AllFilesPredicate,
    AllPredicate,
    AnyFilePredicate,
    AnyPredicate,
    FilePredicate,
    LoadFileAction,
    NotPredicate,
    Predicate,
    ReviewContextConfig,
    ReviewContextValidationError,
    Rule,
    RuleAction,
    parse_review_context_toml,
    tomllib,
)

logger = getLogger(__name__)

_review_context_cache: dict[
    tuple[str, str, str], tuple[float, "ReviewContextConfig"]
] = {}
_REVIEW_CONTEXT_CACHE_TTL = 300  # seconds
DEFAULT_REVIEW_CONTEXT_PATH = "review-context.toml"


@dataclass
class MatchedAction:
    action: RuleAction
    matched_rules: list[str]


class ExternalContentItem(BaseModel):
    name: str
    body: str
    source_type: Literal["github_file", "phabricator_revision", "github_commit"]
    source: str
    action: dict
    matched_rules: list[str]
    trusted: bool = True
    trust_reason: str
    bytes: int
    sha256: str

    @classmethod
    def create(
        cls,
        *,
        name: str,
        body: str,
        source_type: Literal["github_file", "phabricator_revision", "github_commit"],
        source: str,
        action: RuleAction,
        matched_rules: list[str],
        trust_reason: str,
    ) -> "ExternalContentItem":
        encoded = body.encode()
        return cls(
            name=name,
            body=body,
            source_type=source_type,
            source=source,
            action=_action_to_dict(action),
            matched_rules=matched_rules,
            trust_reason=trust_reason,
            bytes=len(encoded),
            sha256=hashlib.sha256(encoded).hexdigest(),
        )

    def manifest(self) -> dict:
        return self.model_dump(exclude={"body"})


def _github_raw_url(repo: str, branch: str, path: str) -> str:
    return (
        f"https://raw.githubusercontent.com/{repo}/refs/heads/{branch}/"
        f"{path.lstrip('/')}"
    )


def _normalise_repo(repo: str) -> str:
    return repo.lower()


def github_repo_allowed(
    repo: str, review_context_repo: str, allowed_repos: set[str]
) -> bool:
    repo = _normalise_repo(repo)
    review_context_repo = _normalise_repo(review_context_repo)
    if repo == review_context_repo:
        return True
    return any(
        repo.startswith(allowed_repo)
        if allowed_repo.endswith("/")
        else repo == allowed_repo
        for allowed_repo in allowed_repos
    )


def _action_to_dict(action: RuleAction) -> dict:
    return {key: value for key, value in action.__dict__.items() if value is not None}


def parse_diff_files(diff: str) -> set[str]:
    """Return the set of file paths added or modified by the diff."""
    try:
        return {f.path for f in PatchSet.from_string(diff) if not f.is_removed_file}
    except Exception:
        files = set()
        for line in diff.splitlines():
            if line.startswith("+++ b/"):
                files.add(line[6:].strip())
        return files


def rule_matches(
    rule: Rule, changed_files: set[str], bug_component: str | None = None
) -> bool:
    return predicate_matches(rule.when, changed_files, bug_component)


def _normalise_path(path: str) -> str:
    path = path.replace("\\", "/")
    if path.startswith("./"):
        return path[2:]
    return path


def _file_matches(predicate: FilePredicate, path: str) -> bool:
    path = _normalise_path(path)
    if predicate.include and not any(
        fnmatch.fnmatchcase(path, pattern) for pattern in predicate.include
    ):
        return False
    if predicate.exclude and any(
        fnmatch.fnmatchcase(path, pattern) for pattern in predicate.exclude
    ):
        return False
    if predicate.ext and not any(path.endswith(ext) for ext in predicate.ext):
        return False
    return True



def _unsupported_metadata_predicate() -> bool:
    return False


def predicate_matches(
    predicate: Predicate,
    changed_files: set[str],
    bug_component: str | None = None,
) -> bool:
    match predicate:
        case AllPredicate(children=children):
            return all(
                predicate_matches(child, changed_files, bug_component)
                for child in children
            )
        case AnyPredicate(children=children):
            return any(
                predicate_matches(child, changed_files, bug_component)
                for child in children
            )
        case NotPredicate(child=child):
            return not predicate_matches(child, changed_files, bug_component)
        case AnyFilePredicate(predicate=file_predicate):
            return any(_file_matches(file_predicate, f) for f in changed_files)
        case AllFilesPredicate(predicate=file_predicate):
            return bool(changed_files) and all(
                _file_matches(file_predicate, f) for f in changed_files
            )
        case _:
            return _unsupported_metadata_predicate()
    raise TypeError(f"Unknown predicate type: {type(predicate).__name__}")


def _action_key(action: RuleAction) -> tuple:
    if isinstance(action, LoadFileAction):
        return (
            "file",
            action.repo or "",
            action.branch or "",
            action.path,
        )
    raise ValueError(f"Unknown action type: {action.type!r}")


def _action_to_content(
    action: LoadFileAction,
    default_repo: str,
    default_branch: str,
    allowed_repos: set[str],
) -> ExternalContent:
    """Resolve a file action to an ExternalContent instance.

    Actions without an explicit repo load from the same GitHub repo and branch
    as the rules file. Cross-repo references use their explicit repo and
    optional branch, defaulting to main.
    """
    path = action.path
    repo = action.repo or default_repo
    branch = action.branch or (default_branch if repo == default_repo else "main")
    if not github_repo_allowed(repo, default_repo, allowed_repos):
        raise ValueError(f"GitHub repo is not allowed for review context: {repo}")
    url = _github_raw_url(repo, branch, path)
    return ExternalContent(name=path, url=url, description=path)


def _merge_rules(base: list[Rule], extra_toml: str) -> list[Rule]:
    """Merge extra rules into the base list.

    Rules in extra_toml whose name matches an existing rule replace that rule
    in-place. Rules with new names are appended. Rules without a name are always
    appended. Returns a new list; base is not mutated.
    """
    rules = list(base)
    extra = parse_review_context_toml(extra_toml).rules
    if not extra:
        return rules
    index = {r.name: i for i, r in enumerate(rules) if r.name}
    for rule in extra:
        name = rule.name
        if name and name in index:
            rules[index[name]] = rule
        else:
            rules.append(rule)
    return rules


def collect_actions(
    diff: str,
    config: ReviewContextConfig,
    bug_component: str | None = None,
    extra_context_toml: str | None = None,
) -> list[MatchedAction]:
    """Return deduplicated actions matched from config against the diff.

    Merges extra_context_toml into config before matching. Evaluates each rule
    against the changed files (and optional bug component). Actions are
    deduplicated across rules so the same file is never fetched twice. Pure —
    no I/O.
    """
    if extra_context_toml:
        config = ReviewContextConfig(
            version=config.version,
            policy=config.policy,
            definitions=config.definitions,
            rules=_merge_rules(config.rules, extra_context_toml),
        )

    changed_files = parse_diff_files(diff)
    logger.debug(
        "Matching rules against %d changed file(s): %s",
        len(changed_files),
        changed_files,
    )

    actions_by_key: dict[tuple, MatchedAction] = {}
    actions: list[MatchedAction] = []

    ordered_rules = sorted(
        enumerate(config.rules), key=lambda item: (-item[1].priority, item[0])
    )
    for _, rule in ordered_rules:
        if not rule_matches(rule, changed_files, bug_component):
            continue
        rule_name = rule.name
        new_actions = []
        for action in rule.load:
            key = _action_key(action)
            if key in actions_by_key:
                actions_by_key[key].matched_rules.append(rule_name)
                continue
            matched_action = MatchedAction(action=action, matched_rules=[rule_name])
            actions_by_key[key] = matched_action
            actions.append(matched_action)
            new_actions.append(action)
        if new_actions:
            logger.debug(
                "Rule %r matched: %d action(s) queued",
                rule_name,
                len(new_actions),
            )

    logger.debug("Total actions to execute: %d", len(actions))
    return actions


async def execute_actions(
    actions: list[MatchedAction],
    default_repo: str,
    default_branch: str,
    allowed_repos: set[str],
    content_overrides: dict[str, str] | None = None,
) -> list[ExternalContentItem]:
    """Execute a list of actions and return loaded external content.

    file actions resolve to an ExternalContent URL and fetch the file content.
    content_overrides bypasses the network fetch for matching names, allowing
    callers to inject test content.
    """
    results: list[ExternalContentItem] = []
    for matched_action in actions:
        action = matched_action.action
        if not isinstance(action, LoadFileAction):
            logger.error("Unsupported review context action %s", _action_to_dict(action))
            continue
        try:
            item = _action_to_content(
                action, default_repo, default_branch, allowed_repos
            )
            if content_overrides and item.name in content_overrides:
                body = content_overrides[item.name]
            else:
                body = await item.load()
            results.append(
                ExternalContentItem.create(
                    name=item.name,
                    body=body,
                    source_type="github_file",
                    source=item.url,
                    action=action,
                    matched_rules=matched_action.matched_rules,
                    trust_reason="github_repo_content",
                )
            )
        except (ValueError, ExternalContentLoadError):
            logger.error(
                "Failed to load content for action %s", _action_to_dict(action)
            )
    return results


async def _fetch_review_context(
    repo: str, branch: str, review_context_path: str
) -> ReviewContextConfig:
    """Fetch and parse review-context.toml, with a short in-process TTL cache."""
    now = time.time()
    cache_key = (repo, branch, review_context_path)
    if cache_key in _review_context_cache:
        ts, config = _review_context_cache[cache_key]
        if now - ts < _REVIEW_CONTEXT_CACHE_TTL:
            logger.debug(
                "Using cached review context from %s@%s:%s (age %.0fs)",
                repo,
                branch,
                review_context_path,
                now - ts,
            )
            return config
    review_context_url = _github_raw_url(repo, branch, review_context_path)
    response = await _fetch_url(review_context_url)
    config = parse_review_context_toml(response.text)
    _review_context_cache[cache_key] = (now, config)
    return config


async def load_external_content_for_diff(
    diff: str,
    review_context_repo: str,
    review_context_branch: str = "main",
    review_context_path: str = DEFAULT_REVIEW_CONTEXT_PATH,
    extra_context_toml: str | None = None,
    content_overrides: dict[str, str] | None = None,
) -> list[ExternalContentItem]:
    """Fetch review-context.toml from GitHub, match against diff, return content.

    Retries the rules fetch on transient errors (via _fetch_url). The parsed
    config is cached in-process with a short TTL to avoid redundant fetches
    across back-to-back reviews.
    """
    try:
        config = await _fetch_review_context(
            review_context_repo, review_context_branch, review_context_path
        )
    except httpx.HTTPError:
        logger.error(
            "Could not fetch review context from %s@%s:%s",
            review_context_repo,
            review_context_branch,
            review_context_path,
        )
        return []
    except (tomllib.TOMLDecodeError, ReviewContextValidationError):
        logger.exception(
            "Could not parse review context from %s@%s:%s",
            review_context_repo,
            review_context_branch,
            review_context_path,
        )
        return []

    try:
        actions = collect_actions(diff, config, extra_context_toml=extra_context_toml)
    except (tomllib.TOMLDecodeError, ReviewContextValidationError):
        logger.exception("Could not parse extra review context")
        return []

    return await execute_actions(
        actions,
        review_context_repo,
        review_context_branch,
        set(config.policy.github.allowed_repos),
        content_overrides,
    )


def external_content_manifest(content_items: list[ExternalContentItem]) -> list[dict]:
    return [item.manifest() for item in content_items]


def format_external_content(content_items: list[ExternalContentItem]) -> str:
    manifest = json.dumps(external_content_manifest(content_items), indent=2)
    content = "\n\n".join(
        f'<context name="{item.name}">\n{item.body.strip()}\n</context>'
        for item in content_items
    )
    return (
        "\n\n<external_content_manifest>\n"
        f"{manifest}\n"
        "</external_content_manifest>"
        "\n\n<external_context>\n"
        f"{content}\n"
        "</external_context>"
    )
