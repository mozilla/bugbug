"""The read tools, filtered to what a logged-out reader of Bugzilla can see.

Why: an agent that posts public comments can repeat in a comment what it read. The API
key belongs to a bot account that the deploy chooses. That account can read more than
the public can: security bugs, and private comments and attachments on public bugs. For
example, a duplicate search can return a restricted security bug about the same crash.
If the agent names that bug and its cause in a public comment, everyone learns about an
unfixed security problem. A private comment can also hold a crash dump or personal data.

A rule in the prompt is not enough. The model can break the rule, and text in a bug can
trick the model. This filter is code that the model cannot change. The agent cannot leak
what it never read.

What: these tools drop the bugs that have a group, and the private comments and
attachments. They refuse the comments and the attachments of a bug that has a group. An
item that does not say that it is public is private.

A bug read returns only the fields in `ALLOWED_FIELDS`. The tools cannot filter nested
comments, attachments or history, so they refuse those fields. `description` comes from
comment 0 only when that comment is public. The history of a public bug comes from its
own tool, as field names with no values. That tool keeps only the changes that a
logged-out reader sees. It drops time tracking, changes to private comments and
attachments, and the change that made a comment private. It also drops a relation change
that names only hidden bugs.

A search refuses the terms that match private comments or attachments. `longdesc`
searches comment text. It is allowed unless `comment_search` is off. If the API key can
read private comments, a match on a public bug tells one bit about them.

This filter is one mode of the broker. An agent whose output is not public uses the
default mode, which serves all that the API key can read. A later mode could allow some
restricted bugs, for example the bugs of one security group. That mode is for an agent
that writes only in that group. For it, a version of `is_public` would take a list of
allowed groups. `BrokerSettings` would get a setting with that list. The server, the
tool list and the agent side would not change.
"""

import dataclasses
import functools
import re
from typing import Annotated, Any

from agent_tools import bugzilla
from agent_tools.bugzilla import BugzillaContext
from agent_tools.registry import ToolDefinition, ToolError
from pydantic import Field

from bugzilla_broker.visibility import marked_public

GROUPS = "groups"
DESCRIPTION = "description"

# Fields a logged-out reader sees on a public bug. `_default`, `_all` and `_extra`
# expand to nested attachments, comments and history that the broker cannot filter.
ALLOWED_FIELDS = frozenset(
    {
        "alias",
        "assigned_to",
        "assigned_to_detail",
        "blocks",
        "cc",
        "cc_detail",
        "classification",
        "component",
        "creation_time",
        "creator",
        "creator_detail",
        "depends_on",
        DESCRIPTION,
        "dupe_of",
        "duplicates",
        "flags",
        GROUPS,
        "id",
        "is_confirmed",
        "is_open",
        "keywords",
        "last_change_time",
        "last_change_time_non_bot",
        "mentors",
        "mentors_detail",
        "op_sys",
        "platform",
        "priority",
        "product",
        "qa_contact",
        "qa_contact_detail",
        "regressed_by",
        "regressions",
        "resolution",
        "see_also",
        "severity",
        "status",
        "summary",
        "target_milestone",
        "triage_owner",
        "type",
        "url",
        "version",
        "votes",
        "whiteboard",
    }
)
CUSTOM_FIELD_PREFIX = "cf_"

# Search terms on private comments and attachments. `longdesc` is `COMMENT_SEARCH`.
REFUSED_SEARCH_FIELDS = frozenset(
    {"commenter", "comment_tag", "content", "quicksearch"}
)
REFUSED_SEARCH_PREFIXES = ("attachments.", "attach_data.", "longdescs.")
COMMENT_SEARCH = "longdesc"
_CHART_FIELD = re.compile(r"^(f\d+|field\d+-\d+-\d+)$")

DEFAULT_ATTACHMENT_HINT = (
    "This broker cannot download attachments. Ask the agent that started you for the "
    "attachment, by its id."
)


def upstream_fields(
    include_fields: str | list | None,
) -> tuple[str, frozenset[str], bool]:
    """The field list to send, the fields it adds, and whether it asks for `description`.

    `id` and `groups` are always sent. `description` is never sent. The broker fills it
    from comment 0 when that comment is public.
    """
    if isinstance(include_fields, list):
        include_fields = ",".join(str(f) for f in include_fields)
    names = [
        f.strip()
        for f in (include_fields or bugzilla.GET_BUGS_DEFAULT_FIELDS).split(",")
        if f.strip()
    ]
    refused = [
        f
        for f in names
        if f not in ALLOWED_FIELDS and not f.startswith(CUSTOM_FIELD_PREFIX)
    ]
    if refused:
        raise ToolError(
            f"fields {', '.join(refused)} are not served",
            payload={
                "error": "field_not_allowed",
                "fields": refused,
                "hint": (
                    "List the fields you need by name. Use get_bug_comments and "
                    "get_bug_attachments for comments and attachments."
                ),
            },
        )
    wants_description = DESCRIPTION in names
    sent = [f for f in names if f != DESCRIPTION]
    added = [f for f in (GROUPS, "id") if f not in sent]
    return ",".join(sent + added), frozenset(added), wants_description


def refused_search_fields(
    params: dict[str, Any], comment_search: bool = True
) -> list[str]:
    """The search params, or boolean chart fields, that read private content.

    `longdesc` searches the text of all comments. It is refused when ``comment_search``
    is off.
    """
    named = list(params)
    for key, value in params.items():
        if _CHART_FIELD.match(key):
            named += value if isinstance(value, list) else [value]
    return [
        str(n)
        for n in named
        if (low := str(n).lower()) in REFUSED_SEARCH_FIELDS
        or low.startswith(REFUSED_SEARCH_PREFIXES)
        or (not comment_search and low == COMMENT_SEARCH)
    ]


def is_public(bug: dict) -> bool:
    return bug.get(GROUPS) == []


def public_bugs(bugs: list[dict]) -> list[dict]:
    public = [b for b in bugs if is_public(b)]
    for b in public:
        if "comments" in b:
            b["comments"] = public_items(b["comments"])
    return public


def drop_fields(bugs: list[dict], fields: frozenset[str]) -> None:
    for b in bugs:
        for f in fields:
            b.pop(f, None)


def public_items(items: list[dict]) -> list[dict]:
    """The comments or attachments marked public."""
    return [i for i in items if marked_public(i)]


def first_comment_text(comments: list[dict]) -> str | None:
    zero = next((c for c in public_items(comments) if c.get("count") == 0), None)
    return zero.get("text") if zero else None


async def _add_descriptions(ctx: BugzillaContext, bugs: list[dict]) -> None:
    """Set `description` to the text of each bug's comment 0, when it is public."""
    unread = [b["id"] for b in bugs if "comments" not in b]
    fetched: dict[int, list[dict]] = {}
    if unread:
        res = await bugzilla.get_bugs(ctx, unread, "id", include_comments=True)
        fetched = {b["id"]: b.get("comments", []) for b in res["bugs"]}
    for b in bugs:
        text = first_comment_text(b.get("comments") or fetched.get(b["id"], []))
        if text is not None:
            b[DESCRIPTION] = text


async def _serve(
    ctx: BugzillaContext,
    bugs: list[dict],
    added: frozenset[str],
    wants_description: bool,
) -> list[dict]:
    """The public bugs, with `description` filled when asked and the added fields gone."""
    bugs = public_bugs(bugs)
    if wants_description:
        await _add_descriptions(ctx, bugs)
    drop_fields(bugs, added)
    return bugs


async def _require_public(ctx: BugzillaContext, bug_id: int) -> None:
    result = await bugzilla.get_bugs(ctx, [bug_id], f"id,{GROUPS}")
    if not public_bugs(result["bugs"]):
        raise ToolError(
            f"bug {bug_id} is not public",
            payload={
                "error": "access_denied",
                "bug_id": bug_id,
                "hint": "This broker serves public bugs only. Skip it.",
            },
        )


def _public_tool(
    original, handler, description: str | None = None, own_schema: bool = False
) -> ToolDefinition:
    """`original`'s tool, served by `handler`.

    With `own_schema`, the argument schema comes from `handler`'s signature.
    """
    defn = next(t for t in bugzilla.TOOLS if t.handler is original)
    wrapped = handler if own_schema else functools.wraps(original)(handler)
    return dataclasses.replace(
        defn, handler=wrapped, description=description or defn.description
    )


def _search_bugs(comment_search: bool):
    hint = "These terms match private comments or attachments. Search summaries" + (
        ", or comment text with `longdesc`." if comment_search else "."
    )

    async def search_bugs(ctx: BugzillaContext, params: dict[str, Any]) -> dict:
        refused = refused_search_fields(params, comment_search)
        if refused:
            raise ToolError(
                f"search terms {', '.join(refused)} are not served",
                payload={
                    "error": "search_field_not_allowed",
                    "fields": refused,
                    "hint": hint,
                },
            )
        params = dict(params)
        params["include_fields"], added, wants_description = upstream_fields(
            params.get("include_fields")
        )
        result = await bugzilla.search_bugs(ctx, params)
        bugs = await _serve(ctx, result["bugs"], added, wants_description)
        return dict(result, count=len(bugs), bugs=bugs)

    return search_bugs


async def _get_bugs(
    ctx: BugzillaContext,
    ids: Annotated[list[int], Field(description="Bug IDs to fetch.")],
    include_fields: Annotated[
        str | None,
        Field(
            description=(
                "Comma-separated field names. `_default`, `_all` and `_extra` are "
                "refused, and so are comments, attachments and history. Defaults "
                "to the agent-tools default fields."
            )
        ),
    ] = None,
    include_comments: Annotated[
        bool,
        Field(
            description=(
                "If true, also bulk-fetch the public comments (one extra request "
                "in total)."
            )
        ),
    ] = False,
) -> dict:
    fields, added, wants_description = upstream_fields(include_fields)
    result = await bugzilla.get_bugs(ctx, ids, fields, include_comments)
    returned = {b["id"] for b in result["bugs"] if is_public(b)}
    bugs = await _serve(ctx, result["bugs"], added, wants_description)
    inaccessible = [i for i in ids if i not in returned]
    return dict(result, count=len(bugs), bugs=bugs, inaccessible=inaccessible)


async def _get_bug_comments(ctx: BugzillaContext, bug_id: int) -> dict:
    await _require_public(ctx, bug_id)
    result = await bugzilla.get_bug_comments(ctx, bug_id)
    comments = public_items(result["comments"])
    return dict(result, count=len(comments), comments=comments)


# History fields that BMO hides from a logged-out reader. A relation change shows only
# when it names a bug that the reader can see. A comment that was made private does not
# show that change.
HIDDEN_HISTORY_FIELDS = frozenset(
    {"estimated_time", "remaining_time", "work_time", "deadline"}
)
MADE_PRIVATE = "longdescs.isprivate"
# Bug ids per visibility lookup, so that the request URL stays short.
LOOKUP_CHUNK = 200
RELATION_FIELDS = frozenset(
    {"depends_on", "blocks", "regressions", "regressed_by", "see_also"}
)
_BUG_ID = re.compile(r"\d+")
_LOCAL_BUG_URL = re.compile(r"/show_bug\.cgi\?id=(\d+)")


def _related(change: dict) -> tuple[set[int], bool]:
    """The bug ids that a relation change names, and whether it names another site."""
    values = [v for v in (change.get("added"), change.get("removed")) if v]
    if change.get("field_name") != "see_also":
        return {int(i) for v in values for i in _BUG_ID.findall(str(v))}, False
    ids, external = set(), False
    for url in (u.strip() for v in values for u in str(v).split(",")):
        m = _LOCAL_BUG_URL.search(url)
        if m and "bugzilla.mozilla.org" in url:
            ids.add(int(m.group(1)))
        elif url:
            external = True
    return ids, external


def related_bugs(history: list[dict]) -> set[int]:
    """Every bug id that a relation change in ``history`` names."""
    return {
        i
        for e in history
        for c in e.get("changes") or ()
        if c.get("field_name") in RELATION_FIELDS
        for i in _related(c)[0]
    }


def changed_fields(
    history: list[dict],
    visible_bugs: set[int],
    public_attachments: set[int],
    public_comments: set[int],
) -> list[dict]:
    """Each history entry as its time, its author and the names of the fields it changed.

    The values are dropped. A change shows only when a logged-out reader sees it. So it
    is not a hidden field, and it is not about a private attachment or comment. A
    relation change must name at least one bug in ``visible_bugs``, or a bug on another
    site.
    """
    entries = []
    for e in history:
        fields = []
        for c in e.get("changes") or ():
            name = c.get("field_name")
            if name in HIDDEN_HISTORY_FIELDS:
                continue
            if name == MADE_PRIVATE and str(c.get("added") or "0") != "0":
                continue
            if "attachment_id" in c and c["attachment_id"] not in public_attachments:
                continue
            if "comment_id" in c and c["comment_id"] not in public_comments:
                continue
            if name in RELATION_FIELDS:
                ids, external = _related(c)
                if not external and not ids & visible_bugs:
                    continue
            fields.append(name)
        if fields:
            entries.append(
                {"when": e.get("when"), "who": e.get("who"), "fields": fields}
            )
    return entries


def _public_ids(ctx: BugzillaContext, path: str, items_of) -> set[int]:
    result = bugzilla._request(ctx, path, {"include_fields": "id,is_private"})
    return {i["id"] for i in public_items(items_of(result))}


async def _get_bug_history(
    ctx: BugzillaContext,
    bug_id: Annotated[int, Field(description="Bug ID.")],
    new_since: Annotated[
        str | None,
        Field(description="Only changes after this time (`2026-10-13T18:01:21Z`)."),
    ] = None,
) -> dict:
    await _require_public(ctx, bug_id)
    params = {"new_since": new_since} if new_since else {}
    result = bugzilla._request(ctx, f"bug/{bug_id}/history", params)
    history = (result.get("bugs") or [{}])[0].get("history") or []
    changes = [c for e in history for c in e.get("changes") or ()]

    related = related_bugs(history)
    visible = set()
    ids = sorted(related)
    for start in range(0, len(ids), LOOKUP_CHUNK):
        found = await bugzilla.get_bugs(
            ctx, ids[start : start + LOOKUP_CHUNK], f"id,{GROUPS}"
        )
        visible |= {b["id"] for b in public_bugs(found["bugs"])}
    attachments = comments = set()
    if any("attachment_id" in c for c in changes):
        attachments = _public_ids(
            ctx,
            f"bug/{bug_id}/attachment",
            lambda r: (r.get("bugs") or {}).get(str(bug_id), []),
        )
    if any("comment_id" in c for c in changes):
        comments = _public_ids(
            ctx,
            f"bug/{bug_id}/comment",
            lambda r: (r.get("bugs") or {}).get(str(bug_id), {}).get("comments", []),
        )
    return {
        "bug_id": bug_id,
        "history": changed_fields(history, visible, attachments, comments),
    }


def with_attachment_hint(note: str, hint: str) -> str:
    """A `data_omitted` note with its download advice replaced by ``hint``."""
    cut = note.find("Use download_attachment")
    if cut == -1:
        return note
    return f"{note[:cut]}{hint}"


def _get_bug_attachments(hint: str):
    async def get_bug_attachments(
        ctx: BugzillaContext,
        bug_id: Annotated[int, Field(description="Bug ID.")],
        include_data: Annotated[
            bool,
            Field(
                description=(
                    "If true, inline base64 content for the public textual "
                    f"attachments under {bugzilla.MAX_INLINE_BYTES // 1024}KiB. Images "
                    "and PDFs are never inlined."
                )
            ),
        ] = False,
    ) -> dict:
        await _require_public(ctx, bug_id)
        result = await bugzilla.get_bug_attachments(ctx, bug_id, include_data)
        attachments = public_items(result["attachments"])
        for att in attachments:
            if "data_omitted" in att:
                att["data_omitted"] = with_attachment_hint(att["data_omitted"], hint)
        return dict(result, count=len(attachments), attachments=attachments)

    return get_bug_attachments


def _attachments_description(hint: str) -> str:
    return f"""Fetch the public attachments of a public bug.

By default, this tool returns only the metadata. With include_data=true, it also
puts the content of small text attachments in the result. The content is base64 in the
'data' field of each attachment. This applies only to text/*, JSON, XML, JavaScript and
SVG, under {bugzilla.MAX_INLINE_BYTES // 1024}KiB, for the first
{bugzilla.MAX_INLINE_ATTACHMENTS} attachments.

Any other attachment keeps its metadata and gets a 'data_omitted' note.
{hint}"""


def public_tools(
    attachment_hint: str = DEFAULT_ATTACHMENT_HINT, comment_search: bool = True
) -> list[ToolDefinition]:
    """The read tools that serve only public content.

    ``attachment_hint`` tells the agent how to get an attachment that is not inlined.
    ``comment_search`` allows `longdesc`, a search of comment text. `download_attachment`
    is left out: it writes the file inside the broker's container.
    """
    return [
        _public_tool(bugzilla.search_bugs, _search_bugs(comment_search)),
        _public_tool(bugzilla.get_bugs, _get_bugs, own_schema=True),
        _public_tool(bugzilla.get_bug_comments, _get_bug_comments),
        _public_tool(
            bugzilla.get_bug_attachments,
            _get_bug_attachments(attachment_hint),
            description=_attachments_description(attachment_hint),
            own_schema=True,
        ),
        ToolDefinition(
            name="get_bug_history",
            namespace="bugzilla",
            description=(
                "Who changed which fields of a public bug, and when. Gives the field "
                "names only, and only the changes that a logged-out reader sees."
            ),
            handler=_get_bug_history,
        ),
    ]
