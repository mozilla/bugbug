# Code Review Skills

The code review agent can load external knowledge at review time, injecting it
as context before the LLM sees the patch. This lets domain experts encode their
knowledge once, in a form the agent uses automatically whenever it reviews
relevant code.

## How it works

When the agent reviews a patch, it:

1. Fetches `review-context.toml` from the root of a GitHub repository passed to
   the review entrypoint as `review_context_repo` (and optionally `review_context_branch`).
2. Parses the changed files from the diff.
3. Matches each rule against the changed files (and optionally the patch's
   associated Bugzilla component, fetched from BMO).
4. For each matched rule, fetches the referenced skill or documentation files.
5. Injects the content as `<context>` blocks inside an `<external_context>`
   section in the review prompt, before the patch.

Pass the GitHub repository containing `review-context.toml` to `patch_review`,
using `org/project` syntax. `review_context_branch` defaults to `main`; pass another
branch when reviewing a backport or another release branch.

```
/patch_review patch_url="https://phabricator.services.mozilla.com/D295935" \
  review_context_repo="mozilla-firefox/firefox" \
  review_context_branch="main"
```

---

## For bugbug developers

### Key files

| File                                                | Purpose                                                            |
| --------------------------------------------------- | ------------------------------------------------------------------ |
| `bugbug/tools/code_review/review_context_schema.py` | Stdlib-only rule schema and validator CLI                          |
| `bugbug/tools/code_review/review_context.py`        | Rule engine: parses rules, matches files, loads external content   |
| `bugbug/tools/code_review/data_types.py`            | `Skill` model with async HTTP fetch and frontmatter stripping      |
| `bugbug/tools/code_review/agent.py`                 | Calls `load_external_content_for_diff` in `run()`, formats context |
| `bugbug/tools/code_review/prompts/system.md`        | System prompt template                                             |
| `bugbug/tools/code_review/prompts/first_message.md` | First user message template (contains `{external_context}` slot)   |

### Prompt templates

Both `system.md` and `first_message.md` are plain Markdown files loaded at
import time. Edit them directly — no Python changes needed. They use Python
`str.format()` placeholders (`{target_software}`, `{patch}`,
`{external_context}`, etc.).

The `{external_context}` slot in `first_message.md` is filled by
`format_external_content()` from `review_context.py` when rules match. It is an
empty string otherwise, so no blank section appears in the prompt.

Loaded content is preceded by an `<external_content_manifest>` block. The
manifest excludes the body text and records each item's name, source, source
type, matched rules, trust reason, byte count, and SHA-256 digest. The same
manifest is also returned in `CodeReviewToolResponse.details["external_content"]`.

All currently injected external content is trusted:

- `file` content is fetched from a human-reviewed GitHub repository.
- `fetch_revision` content comes from configured review platforms.
- GitHub `file` loads are restricted by the repository allow-list in
  `review-context.toml`; the repository containing the rules file is always
  allowed.

### Caching

The parsed rules file is cached in-process for a short TTL, keyed by
`review_context_repo`, `review_context_branch`, and rules path. External content
is fetched for each review so the audit manifest reflects exactly what was
injected for that review. The shared `httpx.AsyncClient` from
`get_http_client()` handles connection reuse.

### Metadata predicates

Implemented via `libmozdata.Bugzilla` with `include_fields=["product", "component"]`.
The patch's associated bug ID is read from `patch.bug_id` (available on
`PhabricatorPatch`; other patch types pass `None`, which fails closed). The
component string compared against the rule is `"Product::Component"`.

The schema accepts `bugzilla`, `review`, and `patch` predicates. Runtime
matching currently implements `bugzilla.component`; `bugzilla.product`,
`bugzilla.keywords`, `bugzilla.severity`, `review.*`, and `patch.*` are parsed
and validated but fail closed until trusted metadata is wired into the matcher.

### `fetch_revision`

Fetches the raw diff of another revision for use as context. Supports:

- **Phabricator**: `{ type = "fetch_revision", revision = "D12345" }` — calls
  the Phabricator API via `get_phabricator_client()`.
- **GitHub**: `{ type = "fetch_revision", repo = "org/repo", hash = "abc123" }`
  — uses the GitHub API with `Accept: application/vnd.github.diff`.

---

## For Mozilla developers: authoring skills and rules

### Overview

You add two things to your repository:

- One or more **skill or documentation files** — Markdown or RST documents
  with expert knowledge, guidelines, or relevant specs. These can be existing
  docs already in the tree, or new files written specifically as review guides.
- Entries in **`review-context.toml`** at the repo root — rules that say which
  files to load when certain code paths are touched.

The review agent picks these up automatically on the next review of a matching
patch.

### Skill file format

Skill files are Markdown (`.md`); documentation files referenced as context can
also be RST (`.rst`). All formats accept optional YAML frontmatter. The
frontmatter is stripped before injection; only the body reaches the LLM. See an
[existing example on Searchfox](https://searchfox.org/mozilla-central/source/.claude/skills/firefox-desktop-frontend/SKILL.md).

```markdown
---
name: dom-audio
description: Web Audio API review guidance
---

## Web Audio review checklist

- `AudioContext` must not be created on a background thread.
- `AudioNode` lifetimes are graph-managed; do not hold raw pointers.
- Prefer `MediaStreamTrack` over raw PCM where the spec allows it.
```

Conventional location for dedicated skill files:

```
.claude/skills/<component-name>/SKILL.md
```

### `review-context.toml` format

`review-context.toml` is parsed as TOML and then validated against the rule
schema before any rule is matched. Unknown rule fields, unknown action types,
and malformed `fetch_revision` actions are rejected.

You can validate a rules file without running a review:

```
bugbug-validate-review-context review-context.toml
```

The validator implementation lives in
`bugbug/tools/code_review/review_context_schema.py` and intentionally uses only the
Python standard library. A target repository can either run the bugbug CLI or
copy that single file into its own tests to validate `review-context.toml`.
Complete TOML examples in this document are marked as `toml review-context` and
validated by bugbug's test suite to keep the docs and parser from drifting.
For a complete validated rules file, see `docs/code-review-context-example.toml`.

```toml review-context
version = 1

[[rules]]
name = "DOM: Web Audio C++"
when = { any_file = { include = ["dom/media/webaudio/**"], ext = [".cpp", ".h"] } }
load = [
  { type = "file", path = ".claude/skills/dom-audio/SKILL.md" },
]
```

Multiple rules can fire for a single patch. Actions are deduplicated across
rules, so the same file is never fetched twice.

All loaded external content is injected before the patch diff. `priority` is
optional and only affects ordering among loaded external content blocks:
matched rules are ordered by descending priority, defaulting to `0`; ties keep
declaration order. Load entries keep their order within a rule. If the same
resolved source is requested more than once, it is loaded once at its first
ordered position and the audit manifest records every matched rule that
requested it.

Load failures are non-fatal. The loader logs an error and continues with the
remaining loads.

### Rule matching

Each rule has a required `when` predicate. Boolean predicates compose matching:
`all` requires every child to match, `any` requires at least one child to match,
and `not` negates its child.

Path matching is defined over repository-relative paths from the patch, not
over local VCS checkout paths. Paths are normalized to POSIX-style separators
(`/`) with no leading `./`. File predicates use Python `fnmatch.fnmatchcase`, so
matching is case-sensitive on every platform, including Windows and macOS. That
keeps rule behavior tied to Git paths rather than host filesystem casing.

The supported glob syntax is Python `fnmatchcase`: `*`, `?`, and character
classes such as `[ch]` and `[!0-9]`. In Python `fnmatchcase`, `*` can match
`/`, so patterns like `dom/media/**` match everything below that prefix.

File quantifiers are explicit:

```toml
# At least one changed file must match all predicates in the object.
when = { any_file = { include = ["dom/media/**"], ext = [".cpp", ".h"] } }

# Every changed file must match all predicates in the object.
when = { all_files = { ext = [".md", ".rst"] } }
```

`any_file` is the normal implementation-code case. `all_files` is for
patch-shape rules such as docs-only, tests-only, or generated-only changes.

### Reusable predicates

Named predicate definitions let you reuse path sets or metadata groups across
rules. Definitions are referenced explicitly with `ref` and expanded
structurally, as if the referenced table had been written inline.

```toml review-context
version = 1

[definitions.files.media_impl]
include = ["dom/media/**"]
exclude = ["dom/media/test/**", "dom/media/gtest/**"]
ext = [".cpp", ".h", ".mm"]

[[rules]]
name = "Media: implementation"
when = { any_file = { ref = "files.media_impl" } }
load = [
  { type = "file", path = ".claude/skills/dom-media/SKILL.md" },
]
```

Definitions are validated like inline predicates. References are namespace
checked: `files.*` refs are valid only in file predicates, `bugzilla.*` only in
Bugzilla predicates, `review.*` only in review predicates, and `patch.*` only
in patch predicates. Unknown references and references combined with inline
fields are schema errors.

### Metadata trigger fields

File predicates are separate from external metadata predicates. The parser
validates these field names and their value types. At runtime, only
`bugzilla.component` currently affects matching; the other fields are accepted
by the schema for forward-compatible rule files but fail closed.

| Namespace  | Field               | Type            | Runtime behavior                                 |
| ---------- | ------------------- | --------------- | ------------------------------------------------ |
| `bugzilla` | `component`         | list of strings | Matches exact `Product::Component` from Bugzilla |
| `bugzilla` | `product`           | list of strings | Parsed and validated; currently fails closed     |
| `bugzilla` | `keywords`          | list of strings | Parsed and validated; currently fails closed     |
| `bugzilla` | `severity`          | list of strings | Parsed and validated; currently fails closed     |
| `review`   | `author`            | list of strings | Parsed and validated; currently fails closed     |
| `review`   | `reviewer`          | list of strings | Parsed and validated; currently fails closed     |
| `review`   | `blocking_reviewer` | list of strings | Parsed and validated; currently fails closed     |
| `patch`    | `repository`        | list of strings | Parsed and validated; currently fails closed     |
| `patch`    | `is_backport`       | boolean         | Parsed and validated; currently fails closed     |

When review metadata matching is wired up, `reviewer` should match both
blocking and non-blocking reviewers. Use `blocking_reviewer` only when the rule
specifically needs a reviewer whose approval is required before landing.

Example — load a skill when C++ or HTML files change under `dom/media/` (which
catches both implementation files and their tests):

```toml review-context
version = 1

[[rules]]
name = "DOM: Media C++ and tests"
when = { any_file = { include = ["dom/media/**"], ext = [".cpp", ".h", ".html"] } }
load = [
  { type = "file", path = ".claude/skills/dom-media/SKILL.md" },
]
```

### Action types

#### `file` — fetch a file from a GitHub repository

```toml
# File from the same repo and branch as review-context.toml (default)
{ type = "file", path = ".claude/skills/dom-audio/SKILL.md" }

# File from another repo (e.g., cubeb's real-time programming guidelines)
{ type = "file", path = ".claude/skills/real-time-programming/SKILL.md",
  repo = "mozilla/cubeb" }

# Explicit branch
{ type = "file", path = ".claude/skills/dom-audio/SKILL.md",
  repo = "mozilla-firefox/firefox", branch = "main" }
```

Constructs a raw GitHub URL:
`https://raw.githubusercontent.com/{repo}/refs/heads/{branch}/{path}`.
When `repo` is omitted, the repo is the `review_context_repo` passed to the review
entrypoint. Same-repo content uses `review_context_branch`; cross-repo content defaults
to `main` unless the action specifies `branch`.

Any Markdown or RST file in the repository can be referenced — skill files,
architecture docs, READMEs, spec notes, etc.

`kind` is optional on `file` and `fetch_revision` actions. It is a free-form
audit hint recorded in the action object in the external content manifest; the
loader does not interpret it.

#### GitHub repository allow-list

GitHub loads are restricted to keep review context auditable:

- The repository containing `review-context.toml` is always allowed.
- Entries ending in `/`, such as `mozilla/`, allow every repo under that
  organization.
- Entries in `org/repo` form allow a single repo.

```toml
[policy.github]
allowed_repos = [
  "mozilla/",
  "web-platform-tests/wpt",
  "whatwg/html",
]
```

Repository names are normalized to lowercase before checking the policy. If a
`load` action references a disallowed repository, the loader does not fetch it.
The failure is non-fatal: it logs an error, skips that load, and continues with
the remaining loads. Skipped loads are not included in the external content
manifest because no content was injected.

#### `fetch_revision` — diff of another revision as context

Injects the raw diff of a related revision — useful for providing context about
a stack parent or a related patch.

```toml
# Phabricator revision
{ type = "fetch_revision", revision = "D12345" }

# GitHub commit (use a git commit hash — Firefox moved from Mercurial to GitHub)
{ type = "fetch_revision", repo = "padenot/cubeb", hash = "abc123def456" }
```

### Testing your rules and skills

Pass your work-in-progress rules and skill files directly to `patch_review`
via the MCP. Rules are merged by name into the fetched `review-context.toml`
(replacing a rule with the same name, or appending if new). Skill content is
injected directly, bypassing the network fetch for matching names.

From Claude Code, trigger a review with your local files:

```
/patch_review patch_url="https://phabricator.services.mozilla.com/D295935" \
  review_context_repo="mozilla-firefox/firefox" \
  extra_context_toml="$(cat review-context.toml)" \
  content_overrides='{".claude/skills/dom-audio/SKILL.md": "skill content here"}'
```

`content_overrides` is a JSON object (as a string). For multi-line skill files,
build the JSON separately:

```bash
python3 -c "import json,sys; print(json.dumps({'.claude/skills/dom-audio/SKILL.md': open('.claude/skills/dom-audio/SKILL.md').read()}))"
```

Then paste the output as the `content_overrides` value.

This simulates the state as if your changes had already landed — no push
required. If a skill is missing, verify the rule conditions match the patch's
changed files (`any_file` / `all_files` against the actual `+++ b/` paths in
the diff).

### Gotchas

**Rules are evaluated against the post-patch file list.** Paths in file predicates
should match the `+++ b/` filenames in the diff.

**Glob patterns use `fnmatchcase` semantics.** `dom/media/**` matches
`dom/media/foo/bar.cpp`.

**Frontmatter is stripped automatically.** You can put metadata (name,
description, owner) in frontmatter without it polluting the injected context.

**Load failures are non-fatal.** The review proceeds without that content, and
the failure is logged at ERROR level and captured by Sentry. Check the URL
manually if content you expect is not appearing.

**Bugzilla component rules require the patch to have an associated bug.** On
non-Phabricator patches or patches without a bug link, Bugzilla component rules
never match.
