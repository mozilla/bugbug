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
import re
import time
from collections.abc import Mapping
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
    BugzillaPredicate,
    DiffSide,
    FetchRevisionAction,
    FilePredicate,
    LoadFileAction,
    NotPredicate,
    PatchPredicate,
    Predicate,
    ReviewContextConfig,
    ReviewContextValidationError,
    ReviewPredicate,
    Rule,
    RuleAction,
    parse_review_context_toml,
    tomllib,
)

logger = getLogger(__name__)

_PHAB_RE = re.compile(r"D(\d+)$")

_review_context_cache: dict[
    tuple[str, str, str], tuple[float, ReviewContextConfig]
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
    ) -> ExternalContentItem:
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


@dataclass(frozen=True)
class ChangedFile:
    path: str
    # Lines as they were before the patch (context and removed) and after it
    # (context and added), without line endings. Empty when the diff could not
    # be parsed.
    old_lines: tuple[str, ...] = ()
    new_lines: tuple[str, ...] = ()


def parse_changed_files(diff: str) -> dict[str, ChangedFile]:
    """Return the files the diff touches, keyed by path.

    Removed files are included with only their old lines.
    """
    try:
        patch = PatchSet.from_string(diff)
    except Exception:
        logger.warning("Could not parse diff; file contents will not match rules")
        changed = {}
        for line in diff.splitlines():
            if line.startswith("+++ b/"):
                path = line[6:].strip()
                changed[path] = ChangedFile(path=path)
        return changed

    # Git can emit several entries for one path (e.g. a symlink replaced by a
    # regular file: a deletion, then an addition), so lines are merged by path.
    lines_by_path: dict[str, tuple[list[str], list[str]]] = {}
    for patched_file in patch:
        old_lines, new_lines = lines_by_path.setdefault(patched_file.path, ([], []))
        for hunk in patched_file:
            for line in hunk:
                value = line.value.rstrip("\r\n")
                if line.is_added:
                    new_lines.append(value)
                elif line.is_removed:
                    old_lines.append(value)
                elif line.is_context:
                    old_lines.append(value)
                    new_lines.append(value)
    return {
        path: ChangedFile(
            path=path, old_lines=tuple(old_lines), new_lines=tuple(new_lines)
        )
        for path, (old_lines, new_lines) in lines_by_path.items()
    }


def rule_matches(
    rule: Rule,
    changed_files: Mapping[str, ChangedFile],
    bug_component: str | None = None,
) -> bool:
    return predicate_matches(rule.when, changed_files, bug_component)


def _normalise_path(path: str) -> str:
    path = path.replace("\\", "/")
    if path.startswith("./"):
        return path[2:]
    return path


def _diff_matches(
    pattern: re.Pattern[str], side: DiffSide | None, changed: ChangedFile
) -> bool:
    if side == "old":
        lines = changed.old_lines
    elif side == "new":
        lines = changed.new_lines
    else:
        lines = changed.old_lines + changed.new_lines
    return any(pattern.search(line) for line in lines)


def _file_matches(predicate: FilePredicate, changed: ChangedFile) -> bool:
    path = _normalise_path(changed.path)
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
    if predicate.diff_regex is None:
        return True
    return _diff_matches(predicate.diff_regex, predicate.side, changed)


def _bugzilla_matches(
    predicate: BugzillaPredicate, bug_component: str | None = None
) -> bool:
    if predicate.product or predicate.keywords or predicate.severity:
        return False
    if predicate.component:
        return bug_component is not None and bug_component in predicate.component
    return False


def _review_matches(predicate: ReviewPredicate) -> bool:
    return False


def _patch_matches(predicate: PatchPredicate) -> bool:
    return False


def predicate_matches(
    predicate: Predicate,
    changed_files: Mapping[str, ChangedFile],
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
            return any(_file_matches(file_predicate, f) for f in changed_files.values())
        case AllFilesPredicate(predicate=file_predicate):
            return bool(changed_files) and all(
                _file_matches(file_predicate, f) for f in changed_files.values()
            )
        case BugzillaPredicate():
            return _bugzilla_matches(predicate, bug_component)
        case ReviewPredicate():
            return _review_matches(predicate)
        case PatchPredicate():
            return _patch_matches(predicate)
    raise TypeError(f"Unknown predicate type: {type(predicate).__name__}")


def _action_key(action: RuleAction) -> tuple:
    if isinstance(action, LoadFileAction):
        return (
            "file",
            action.repo or "",
            action.branch or "",
            action.path,
        )
    if isinstance(action, FetchRevisionAction):
        return (
            "fetch_revision",
            action.revision or "",
            action.repo or "",
            action.hash or "",
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


async def _fetch_revision(
    action: FetchRevisionAction,
) -> tuple[str, str, Literal["phabricator_revision", "github_commit"], str, str] | None:
    """Fetch the diff of another revision, either from Phabricator or GitHub."""
    revision_str = action.revision or ""
    repo = action.repo or ""
    commit_hash = action.hash or ""

    if revision_str:
        m = _PHAB_RE.match(revision_str.strip())
        if not m:
            logger.warning("fetch_revision: cannot parse revision %r", revision_str)
            return None
        rev_num = int(m.group(1))
        try:
            from bugbug.tools.core.platforms.phabricator import PhabricatorPatch

            patch = PhabricatorPatch(revision_id=rev_num)
            await patch.load()
            content = patch.raw_diff
            name = f"Revision D{rev_num}"
            return (
                name,
                content,
                "phabricator_revision",
                name,
                "phabricator_revision",
            )
        except Exception:
            logger.warning("fetch_revision: failed to load Phabricator D%s", rev_num)
            return None

    if repo and commit_hash:
        try:
            response = await _fetch_url(
                f"https://api.github.com/repos/{repo}/commits/{commit_hash}",
                headers={"Accept": "application/vnd.github.diff"},
            )
            source = f"{repo}@{commit_hash}"
            return (
                f"{repo}@{commit_hash[:12]}",
                response.text,
                "github_commit",
                source,
                "github_repo_content",
            )
        except httpx.HTTPError:
            logger.warning("fetch_revision: failed to load %s@%s", repo, commit_hash)
            return None

    logger.warning(
        "fetch_revision: action has neither revision nor repo+hash: %s", action
    )
    return None


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

    changed_files = parse_changed_files(diff)
    logger.debug(
        "Matching rules against %d changed file(s): %s",
        len(changed_files),
        sorted(changed_files),
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

    fetch_revision actions fetch the raw diff of a Phabricator revision or
    GitHub commit. file actions resolve to an ExternalContent URL and
    fetch the file content. content_overrides bypasses the network fetch for
    matching names, allowing callers to inject test content.
    """
    results: list[ExternalContentItem] = []
    for matched_action in actions:
        action = matched_action.action
        if isinstance(action, FetchRevisionAction):
            result = await _fetch_revision(action)
            if result:
                name, body, source_type, source, trust_reason = result
                results.append(
                    ExternalContentItem.create(
                        name=name,
                        body=body,
                        source_type=source_type,
                        source=source,
                        action=action,
                        matched_rules=matched_action.matched_rules,
                        trust_reason=trust_reason,
                    )
                )
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
        except ValueError, ExternalContentLoadError:
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
    patch=None,
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
    except tomllib.TOMLDecodeError, ReviewContextValidationError:
        logger.exception(
            "Could not parse review context from %s@%s:%s",
            review_context_repo,
            review_context_branch,
            review_context_path,
        )
        return []

    bug_component: str | None = None
    if patch is not None:
        bug_component = await patch.bug_component()

    try:
        actions = collect_actions(diff, config, bug_component, extra_context_toml)
    except tomllib.TOMLDecodeError, ReviewContextValidationError:
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
    content = "\n\n".join(
        f'<context name="{item.name}">\n{item.body.strip()}\n</context>'
        for item in content_items
    )
    return f"\n\n<external_context>\n{content}\n</external_context>"


async def resolve_external_context_for_review(
    patch,
    review_context_repo: str | None = None,
    review_context_branch: str | None = None,
    extra_context_toml: str | None = None,
    content_overrides: dict[str, str] | None = None,
) -> tuple[str, list[dict]]:
    """Resolve the context repository and load matching review context."""
    if review_context_repo is None:
        repo_ref = await patch.github_repo_ref()
        if repo_ref:
            review_context_repo, inferred_branch = repo_ref
            review_context_branch = review_context_branch or inferred_branch
    if not review_context_repo:
        return "", []
    return await load_external_context_for_review(
        patch,
        review_context_repo,
        review_context_branch=review_context_branch or "main",
        extra_context_toml=extra_context_toml,
        content_overrides=content_overrides,
    )


async def load_external_context_for_review(
    patch,
    review_context_repo: str,
    review_context_branch: str = "main",
    extra_context_toml: str | None = None,
    content_overrides: dict[str, str] | None = None,
) -> tuple[str, list[dict]]:
    """Load and format external review context for a patch.

    Returns (formatted_context_str, manifest_list). Both are empty if no repo
    is configured or no rules match.
    """
    content_items = await load_external_content_for_diff(
        patch.raw_diff,
        review_context_repo,
        review_context_branch=review_context_branch,
        patch=patch,
        extra_context_toml=extra_context_toml,
        content_overrides=content_overrides,
    )
    if not content_items:
        return "", []
    return format_external_content(content_items), external_content_manifest(
        content_items
    )
