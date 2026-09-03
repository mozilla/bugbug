# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

"""Schema and validator for code review external context rules.

This module intentionally depends only on the Python standard library so it can
also be copied into target repositories and used from local tests.
"""

import argparse
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, TypeAlias

try:
    import tomllib
except ImportError:
    import tomli as tomllib  # type: ignore


class ReviewContextValidationError(ValueError):
    """Raised when review-context.toml has valid TOML but invalid schema."""


@dataclass(frozen=True)
class GithubPolicy:
    allowed_repos: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Policy:
    github: GithubPolicy = field(default_factory=GithubPolicy)


@dataclass(frozen=True)
class FilePredicate:
    include: list[str] = field(default_factory=list)
    exclude: list[str] = field(default_factory=list)
    ext: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class BugzillaPredicate:
    product: list[str] = field(default_factory=list)
    component: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    severity: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ReviewPredicate:
    author: list[str] = field(default_factory=list)
    reviewer: list[str] = field(default_factory=list)
    blocking_reviewer: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class PatchPredicate:
    repository: list[str] = field(default_factory=list)
    is_backport: bool | None = None


@dataclass(frozen=True)
class Definitions:
    files: dict[str, FilePredicate] = field(default_factory=dict)
    bugzilla: dict[str, BugzillaPredicate] = field(default_factory=dict)
    review: dict[str, ReviewPredicate] = field(default_factory=dict)
    patch: dict[str, PatchPredicate] = field(default_factory=dict)


@dataclass(frozen=True)
class AllPredicate:
    children: list["Predicate"]


@dataclass(frozen=True)
class AnyPredicate:
    children: list["Predicate"]


@dataclass(frozen=True)
class NotPredicate:
    child: "Predicate"


@dataclass(frozen=True)
class AnyFilePredicate:
    predicate: FilePredicate


@dataclass(frozen=True)
class AllFilesPredicate:
    predicate: FilePredicate


Predicate: TypeAlias = (
    AllPredicate
    | AnyPredicate
    | NotPredicate
    | AnyFilePredicate
    | AllFilesPredicate
    | BugzillaPredicate
    | ReviewPredicate
    | PatchPredicate
)


@dataclass(frozen=True)
class LoadFileAction:
    type: Literal["file"]
    path: str
    repo: str | None = None
    branch: str | None = None
    kind: str | None = None


@dataclass(frozen=True)
class FetchRevisionAction:
    type: Literal["fetch_revision"]
    revision: str | None = None
    repo: str | None = None
    hash: str | None = None
    kind: str | None = None


RuleAction: TypeAlias = LoadFileAction | FetchRevisionAction


@dataclass(frozen=True)
class Rule:
    name: str
    when: Predicate
    load: list[RuleAction]
    description: str | None = None
    owners: list[str] = field(default_factory=list)
    priority: int = 0


@dataclass(frozen=True)
class ReviewContextConfig:
    version: int
    policy: Policy = field(default_factory=Policy)
    definitions: Definitions = field(default_factory=Definitions)
    rules: list[Rule] = field(default_factory=list)


def _reject_unknown_keys(path: str, value: dict, allowed: set[str]) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise ReviewContextValidationError(
            f"{path}: unknown field(s): {', '.join(unknown)}"
        )


def _require_table(value: object, path: str) -> dict:
    if not isinstance(value, dict):
        raise ReviewContextValidationError(f"{path}: expected table")
    return value


def _require_string(value: object, path: str) -> str:
    if not isinstance(value, str):
        raise ReviewContextValidationError(f"{path}: expected string")
    return value


def _optional_string(value: object, path: str) -> str | None:
    if value is None:
        return None
    return _require_string(value, path)


def _string_list(value: object, path: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ReviewContextValidationError(f"{path}: expected list of strings")
    for index, item in enumerate(value):
        _require_string(item, f"{path}[{index}]")
    return value


def _required_non_empty_string_list(value: object, path: str) -> list[str]:
    items = _string_list(value, path)
    if not items:
        raise ReviewContextValidationError(f"{path}: expected non-empty list")
    return items


def _optional_bool(value: object, path: str) -> bool | None:
    if value is None:
        return None
    if not isinstance(value, bool):
        raise ReviewContextValidationError(f"{path}: expected boolean")
    return value


def _require_int(value: object, path: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ReviewContextValidationError(f"{path}: expected integer")
    return value


def _normalise_repo(repo: str) -> str:
    return repo.lower()


def _parse_github_policy(value: object, path: str) -> GithubPolicy:
    table = _require_table(value, path)
    _reject_unknown_keys(path, table, {"allowed_repos"})
    return GithubPolicy(
        allowed_repos=[
            _normalise_repo(repo)
            for repo in _string_list(
                table.get("allowed_repos"), f"{path}.allowed_repos"
            )
        ]
    )


def _parse_policy(value: object | None) -> Policy:
    if value is None:
        return Policy()
    table = _require_table(value, "policy")
    _reject_unknown_keys("policy", table, {"github"})
    return Policy(github=_parse_github_policy(table.get("github", {}), "policy.github"))


def _parse_file_predicate(value: object, path: str) -> FilePredicate:
    table = _require_table(value, path)
    _reject_unknown_keys(path, table, {"include", "exclude", "ext", "ref"})
    if "ref" in table:
        if len(table) != 1:
            raise ReviewContextValidationError(
                f"{path}.ref: cannot combine with fields"
            )
        raise ReviewContextValidationError(f"{path}.ref: unresolved ref")
    predicate = FilePredicate(
        include=_string_list(table.get("include"), f"{path}.include"),
        exclude=_string_list(table.get("exclude"), f"{path}.exclude"),
        ext=_string_list(table.get("ext"), f"{path}.ext"),
    )
    if not predicate.include and not predicate.exclude and not predicate.ext:
        raise ReviewContextValidationError(f"{path}: expected include, exclude, or ext")
    return predicate


def _parse_bugzilla_predicate(value: object, path: str) -> BugzillaPredicate:
    table = _require_table(value, path)
    _reject_unknown_keys(
        path, table, {"product", "component", "keywords", "severity", "ref"}
    )
    if "ref" in table:
        if len(table) != 1:
            raise ReviewContextValidationError(
                f"{path}.ref: cannot combine with fields"
            )
        raise ReviewContextValidationError(f"{path}.ref: unresolved ref")
    predicate = BugzillaPredicate(
        product=_string_list(table.get("product"), f"{path}.product"),
        component=_string_list(table.get("component"), f"{path}.component"),
        keywords=_string_list(table.get("keywords"), f"{path}.keywords"),
        severity=_string_list(table.get("severity"), f"{path}.severity"),
    )
    if (
        not predicate.product
        and not predicate.component
        and not predicate.keywords
        and not predicate.severity
    ):
        raise ReviewContextValidationError(f"{path}: expected at least one field")
    return predicate


def _parse_review_predicate(value: object, path: str) -> ReviewPredicate:
    table = _require_table(value, path)
    _reject_unknown_keys(
        path, table, {"author", "reviewer", "blocking_reviewer", "ref"}
    )
    if "ref" in table:
        if len(table) != 1:
            raise ReviewContextValidationError(
                f"{path}.ref: cannot combine with fields"
            )
        raise ReviewContextValidationError(f"{path}.ref: unresolved ref")
    predicate = ReviewPredicate(
        author=_string_list(table.get("author"), f"{path}.author"),
        reviewer=_string_list(table.get("reviewer"), f"{path}.reviewer"),
        blocking_reviewer=_string_list(
            table.get("blocking_reviewer"), f"{path}.blocking_reviewer"
        ),
    )
    if (
        not predicate.author
        and not predicate.reviewer
        and not predicate.blocking_reviewer
    ):
        raise ReviewContextValidationError(f"{path}: expected at least one field")
    return predicate


def _parse_patch_predicate(value: object, path: str) -> PatchPredicate:
    table = _require_table(value, path)
    _reject_unknown_keys(path, table, {"repository", "is_backport", "ref"})
    if "ref" in table:
        if len(table) != 1:
            raise ReviewContextValidationError(
                f"{path}.ref: cannot combine with fields"
            )
        raise ReviewContextValidationError(f"{path}.ref: unresolved ref")
    predicate = PatchPredicate(
        repository=_string_list(table.get("repository"), f"{path}.repository"),
        is_backport=_optional_bool(table.get("is_backport"), f"{path}.is_backport"),
    )
    if not predicate.repository and predicate.is_backport is None:
        raise ReviewContextValidationError(f"{path}: expected at least one field")
    return predicate


def _parse_definitions(value: object | None) -> Definitions:
    if value is None:
        return Definitions()
    table = _require_table(value, "definitions")
    _reject_unknown_keys("definitions", table, {"files", "bugzilla", "review", "patch"})
    return Definitions(
        files={
            name: _parse_file_predicate(definition, f"definitions.files.{name}")
            for name, definition in _require_table(
                table.get("files", {}), "definitions.files"
            ).items()
        },
        bugzilla={
            name: _parse_bugzilla_predicate(definition, f"definitions.bugzilla.{name}")
            for name, definition in _require_table(
                table.get("bugzilla", {}), "definitions.bugzilla"
            ).items()
        },
        review={
            name: _parse_review_predicate(definition, f"definitions.review.{name}")
            for name, definition in _require_table(
                table.get("review", {}), "definitions.review"
            ).items()
        },
        patch={
            name: _parse_patch_predicate(definition, f"definitions.patch.{name}")
            for name, definition in _require_table(
                table.get("patch", {}), "definitions.patch"
            ).items()
        },
    )


def _resolve_ref(ref: object, namespace: str, definitions: Definitions, path: str):
    ref_name = _require_string(ref, path)
    prefix = f"{namespace}."
    if not ref_name.startswith(prefix):
        raise ReviewContextValidationError(
            f"{path}: expected {namespace}.* ref, got {ref_name!r}"
        )
    name = ref_name[len(prefix) :]
    mapping = getattr(definitions, namespace)
    try:
        return mapping[name]
    except KeyError:
        raise ReviewContextValidationError(
            f"{path}: unknown ref {ref_name!r}"
        ) from None


def _parse_file_predicate_ref(
    value: object, path: str, definitions: Definitions
) -> FilePredicate:
    table = _require_table(value, path)
    if "ref" in table:
        if len(table) != 1:
            raise ReviewContextValidationError(
                f"{path}.ref: cannot combine with fields"
            )
        return _resolve_ref(table["ref"], "files", definitions, f"{path}.ref")
    return _parse_file_predicate(value, path)


def _parse_bugzilla_predicate_ref(
    value: object, path: str, definitions: Definitions
) -> BugzillaPredicate:
    table = _require_table(value, path)
    if "ref" in table:
        if len(table) != 1:
            raise ReviewContextValidationError(
                f"{path}.ref: cannot combine with fields"
            )
        return _resolve_ref(table["ref"], "bugzilla", definitions, f"{path}.ref")
    return _parse_bugzilla_predicate(value, path)


def _parse_review_predicate_ref(
    value: object, path: str, definitions: Definitions
) -> ReviewPredicate:
    table = _require_table(value, path)
    if "ref" in table:
        if len(table) != 1:
            raise ReviewContextValidationError(
                f"{path}.ref: cannot combine with fields"
            )
        return _resolve_ref(table["ref"], "review", definitions, f"{path}.ref")
    return _parse_review_predicate(value, path)


def _parse_patch_predicate_ref(
    value: object, path: str, definitions: Definitions
) -> PatchPredicate:
    table = _require_table(value, path)
    if "ref" in table:
        if len(table) != 1:
            raise ReviewContextValidationError(
                f"{path}.ref: cannot combine with fields"
            )
        return _resolve_ref(table["ref"], "patch", definitions, f"{path}.ref")
    return _parse_patch_predicate(value, path)


def _parse_all_predicate(
    value: object, path: str, definitions: Definitions
) -> Predicate:
    return AllPredicate(_parse_predicate_list(value, path, definitions))


def _parse_any_predicate(
    value: object, path: str, definitions: Definitions
) -> Predicate:
    return AnyPredicate(_parse_predicate_list(value, path, definitions))


def _parse_not_predicate(
    value: object, path: str, definitions: Definitions
) -> Predicate:
    return NotPredicate(_parse_predicate(value, path, definitions))


def _parse_any_file_predicate(
    value: object, path: str, definitions: Definitions
) -> Predicate:
    return AnyFilePredicate(_parse_file_predicate_ref(value, path, definitions))


def _parse_all_files_predicate(
    value: object, path: str, definitions: Definitions
) -> Predicate:
    return AllFilesPredicate(_parse_file_predicate_ref(value, path, definitions))


PredicateParser: TypeAlias = Callable[[object, str, Definitions], Predicate]

_PREDICATE_PARSERS: dict[str, PredicateParser] = {
    "all": _parse_all_predicate,
    "any": _parse_any_predicate,
    "not": _parse_not_predicate,
    "any_file": _parse_any_file_predicate,
    "all_files": _parse_all_files_predicate,
    "bugzilla": _parse_bugzilla_predicate_ref,
    "review": _parse_review_predicate_ref,
    "patch": _parse_patch_predicate_ref,
}


def _parse_predicate(value: object, path: str, definitions: Definitions) -> Predicate:
    """Parse one predicate node.

    Each TOML predicate object must have exactly one key. Dispatching through a
    table keeps the grammar visible and makes adding a predicate a one-line
    change plus a parser function.
    """
    table = _require_table(value, path)
    keys = set(table)
    _reject_unknown_keys(path, table, set(_PREDICATE_PARSERS))
    if len(keys) != 1:
        raise ReviewContextValidationError(f"{path}: expected exactly one predicate")
    key = next(iter(keys))
    return _PREDICATE_PARSERS[key](table[key], f"{path}.{key}", definitions)


def _parse_predicate_list(
    value: object, path: str, definitions: Definitions
) -> list[Predicate]:
    if not isinstance(value, list) or not value:
        raise ReviewContextValidationError(f"{path}: expected non-empty list")
    return [
        _parse_predicate(child, f"{path}[{index}]", definitions)
        for index, child in enumerate(value)
    ]


def _parse_action(value: object, path: str) -> RuleAction:
    """Parse one load action.

    Load actions are intentionally small: validate the schema here, and leave
    trust policy and network behavior to the runtime loader.
    """
    table = _require_table(value, path)
    action_type = table.get("type")
    if action_type == "file":
        _reject_unknown_keys(path, table, {"type", "path", "repo", "branch", "kind"})
        if "path" not in table:
            raise ReviewContextValidationError(f"{path}.path: required for file")
        return LoadFileAction(
            type="file",
            path=_require_string(table["path"], f"{path}.path"),
            repo=_optional_string(table.get("repo"), f"{path}.repo"),
            branch=_optional_string(table.get("branch"), f"{path}.branch"),
            kind=_optional_string(table.get("kind"), f"{path}.kind"),
        )
    if action_type == "fetch_revision":
        _reject_unknown_keys(path, table, {"type", "revision", "repo", "hash", "kind"})
        revision = _optional_string(table.get("revision"), f"{path}.revision")
        repo = _optional_string(table.get("repo"), f"{path}.repo")
        commit_hash = _optional_string(table.get("hash"), f"{path}.hash")
        if not revision and not (repo and commit_hash):
            raise ReviewContextValidationError(
                f"{path}: fetch_revision requires revision or repo+hash"
            )
        return FetchRevisionAction(
            type="fetch_revision",
            revision=revision,
            repo=repo,
            hash=commit_hash,
            kind=_optional_string(table.get("kind"), f"{path}.kind"),
        )
    if action_type is None:
        raise ReviewContextValidationError(f"{path}.type: required")
    raise ReviewContextValidationError(
        f"{path}.type: unknown action type {action_type!r}"
    )


def _parse_rule(value: object, index: int, definitions: Definitions) -> Rule:
    path = f"rules[{index}]"
    table = _require_table(value, path)
    _reject_unknown_keys(
        path,
        table,
        {"name", "description", "owners", "priority", "when", "load"},
    )
    if "name" not in table:
        raise ReviewContextValidationError(f"{path}.name: required")
    if "when" not in table:
        raise ReviewContextValidationError(f"{path}.when: required")
    if "load" not in table:
        raise ReviewContextValidationError(f"{path}.load: required")
    load_value = table["load"]
    if not isinstance(load_value, list) or not load_value:
        raise ReviewContextValidationError(f"{path}.load: expected non-empty list")
    return Rule(
        name=_require_string(table["name"], f"{path}.name"),
        description=_optional_string(table.get("description"), f"{path}.description"),
        owners=_string_list(table.get("owners"), f"{path}.owners"),
        priority=_require_int(table.get("priority", 0), f"{path}.priority"),
        when=_parse_predicate(table["when"], f"{path}.when", definitions),
        load=[
            _parse_action(action, f"{path}.load[{action_index}]")
            for action_index, action in enumerate(load_value)
        ],
    )


def parse_review_context_data(data: dict) -> ReviewContextConfig:
    """Validate parsed review-context.toml data."""
    _reject_unknown_keys("<root>", data, {"version", "policy", "definitions", "rules"})
    version = data.get("version")
    if version != 1:
        raise ReviewContextValidationError("version: expected 1")
    policy = _parse_policy(data.get("policy"))
    definitions = _parse_definitions(data.get("definitions"))
    rules_value = data.get("rules", [])
    if not isinstance(rules_value, list):
        raise ReviewContextValidationError("rules: expected list of rule tables")
    return ReviewContextConfig(
        version=version,
        policy=policy,
        definitions=definitions,
        rules=[
            _parse_rule(rule, index, definitions)
            for index, rule in enumerate(rules_value)
        ],
    )


def parse_review_context_toml(text: str) -> ReviewContextConfig:
    """Parse and validate review-context.toml content."""
    return parse_review_context_data(tomllib.loads(text))


def validate_review_context_file(path: str | Path) -> ReviewContextConfig:
    """Parse and validate a review-context.toml file."""
    return parse_review_context_toml(Path(path).read_text())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate review-context.toml")
    parser.add_argument(
        "path",
        nargs="?",
        default="review-context.toml",
        help="Path to review-context.toml (default: %(default)s)",
    )
    args = parser.parse_args(argv)

    try:
        config = validate_review_context_file(args.path)
    except (OSError, tomllib.TOMLDecodeError, ReviewContextValidationError) as exc:
        print(f"{args.path}: invalid: {exc}", file=sys.stderr)
        return 1

    print(f"{args.path}: valid ({len(config.rules)} rule(s))")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
