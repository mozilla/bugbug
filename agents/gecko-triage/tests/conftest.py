import copy
import datetime as dt
import functools
import inspect
import json
import os
import re
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

# The tests run as the media team unless they use `no_team`. Set before the agent's
# config is imported, which reads it.
os.environ.setdefault("TEAM", "media")

import hackbot_agents.gecko_triage.__main__  # noqa: E402, F401
from claude_agent_sdk import ResultMessage  # noqa: E402
from hackbot_agents.gecko_triage import (  # noqa: E402
    agent,
    broker_client,
    closing,
    config,
)
from hackbot_agents.gecko_triage.closing import (  # noqa: E402
    CloseVerdict,
    CloseVerdicts,
)
from hackbot_agents.gecko_triage.config import (  # noqa: E402
    BOT_ACCOUNT,
    TEAM_META_BUGS,
    TRIAGE_SCOPE,
)
from hackbot_agents.gecko_triage.hooks import install_hooks  # noqa: E402
from hackbot_agents.gecko_triage.investigations import Investigations  # noqa: E402
from hackbot_agents.gecko_triage.state import TriageState  # noqa: E402
from hackbot_runtime.actions import ActionsRecorder  # noqa: E402
from hackbot_runtime.actions import bugzilla as runtime_bugzilla  # noqa: E402
from hackbot_runtime.actions.bugzilla import COMMENT_FOOTER_SEPARATOR  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"


@functools.cache
def _read(name: str):
    return json.loads((FIXTURES / name).read_text())


def load(name: str):
    return copy.deepcopy(_read(name))


# A fixed clock: the needinfo ages in the tests are relative to this.
NOW = dt.datetime(2026, 10, 15, 12, 0, tzinfo=dt.timezone.utc)
# A month on: a needinfo set with the bot's comment of the 13th is stale by then.
LATER = dt.datetime(2026, 11, 15, 12, 0, tzinfo=dt.timezone.utc)
# The time of the bot's comment in the returning fixture. A needinfo set then is fresh
# at NOW and stale at LATER.
BOT_COMMENTED = "2026-10-13T09:00:00Z"


def with_needinfo(
    bug: dict, requestee: str, since: str = BOT_COMMENTED, setter: str = BOT_ACCOUNT
) -> dict:
    """``bug`` with one more open needinfo."""
    flag = {
        "name": "needinfo",
        "status": "?",
        "requestee": requestee,
        "setter": setter,
        "creation_date": since,
    }
    return dict(bug, flags=[*(bug.get("flags") or ()), flag])


def quiet(bug: dict, **times) -> dict:
    """``bug`` as it stands right after the bot's comment, unless ``times`` say more."""
    return {**bug, "last_change_time_non_bot": BOT_COMMENTED, **times}


def fake_broker(monkeypatch, responses: dict) -> list:
    """A broker that answers each tool from ``responses``; the calls it got, in order.

    An answer is a JSON body, an Exception for an error reply, or an async function of
    the args that gives the body.
    """
    calls = []

    @asynccontextmanager
    async def client(url):
        calls.append(("connect", url))
        yield "read", "write", None

    class Session:
        def __init__(self, read, write):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def initialize(self):
            pass

        async def call_tool(self, tool, args):
            calls.append((tool, args))
            body = responses[tool]
            if callable(body):
                body = await body(args)
            if isinstance(body, Exception):
                return SimpleNamespace(
                    isError=True, content=[SimpleNamespace(text=str(body))]
                )
            return SimpleNamespace(
                isError=False, content=[SimpleNamespace(text=json.dumps(body))]
            )

    monkeypatch.setattr(broker_client, "streamablehttp_client", client)
    monkeypatch.setattr(broker_client, "ClientSession", Session)
    return calls


@pytest.fixture
def bug_new() -> dict:
    return load("bug_new.json")["bugs"][0]


@pytest.fixture
def comments_new() -> list[dict]:
    return load("comments_new.json")["bugs"]["9000001"]["comments"]


@pytest.fixture
def comments_returning() -> list[dict]:
    return load("comments_returning.json")["bugs"]["9000001"]["comments"]


@pytest.fixture
def new_state(bug_new, comments_new) -> TriageState:
    return TriageState(bug=bug_new, comments=comments_new, now=NOW)


@pytest.fixture
def returning_state(bug_new, comments_returning) -> TriageState:
    """The bot's tagged comment with an old footer, then the reporter's answer."""
    bug = dict(bug_new, last_change_time_non_bot="2026-10-14T10:00:00Z")
    return TriageState(bug=bug, comments=comments_returning, now=NOW)


# What `config.py` sets when `TEAM` is unset; `test_no_team.py` checks the import itself.
NO_TEAM = {
    "TEAM": None,
    "TEAM_DIR": None,
    "TEAM_RULES_DIR": None,
    "CAPTURE": None,
    "TRIAGE_SCOPE": (),
    "SCOPE_KEYS": frozenset(),
    "NEEDINFO_EXPERTS": frozenset(),
    "NEEDINFO_FALLBACK": frozenset(),
    "ISSUE_TYPES": frozenset(),
    "TEAM_META_BUGS": frozenset(),
}


@pytest.fixture
def no_team(monkeypatch):
    """A run with no team, in every agent module that holds a team value.

    A module that imports a team value from `config` holds the same object, so each
    attribute that is one of those objects is patched.
    """
    originals = {name: getattr(config, name) for name in NO_TEAM}
    patched = []
    for module_name, module in list(sys.modules.items()):
        if not module_name.startswith("hackbot_agents.gecko_triage"):
            continue
        for attr, value in list(vars(module).items()):
            for name, original in originals.items():
                if value is original:
                    monkeypatch.setattr(module, attr, NO_TEAM[name])
                    patched.append((module_name, attr))
                    break
    for name in NO_TEAM:
        assert ("hackbot_agents.gecko_triage.config", name) in patched, name
    assert ("hackbot_agents.gecko_triage.state", "NEEDINFO_EXPERTS") in patched
    for module in ("agent", "dupes", "hooks", "preflight"):
        assert (f"hackbot_agents.gecko_triage.{module}", "TEAM") in patched, module


class FakeBugzilla:
    """A Bugzilla search over ``bugs``, for the boolean charts the duplicate search sends.

    Each bug carries its `comments` as plain strings. Only the operators the search uses
    are known, so a new operator fails the test instead of matching everything.
    """

    TEXT = {"short_desc": "summary", "cf_crash_signature": "cf_crash_signature"}

    def __init__(self, bugs: list[dict]):
        self.bugs = bugs
        self.calls: list[dict] = []

    def _texts(self, bug: dict, field: str) -> list[str]:
        if field == "longdesc":
            return list(bug.get("comments") or ())
        if field == "bug_id":
            return [str(bug["id"])]
        return [bug.get(self.TEXT[field]) or ""]

    def _matches(self, bug: dict, params: dict) -> bool:
        if bug["product"] not in params["product"]:
            return False
        if bug["component"] not in params["component"]:
            return False
        n = 1
        while f"f{n}" in params:
            field, op, value = params[f"f{n}"], params[f"o{n}"], params[f"v{n}"]
            texts = self._texts(bug, field)
            if op == "regexp":
                hit = any(re.search(value, t, re.IGNORECASE) for t in texts)
            elif op == "substring":
                hit = any(value.lower() in t.lower() for t in texts)
            elif op == "notequals":
                hit = str(value) not in texts
            else:
                raise AssertionError(f"unknown operator {op}")
            if not hit:
                return False
            n += 1
        return True

    async def search(self, params: dict) -> list[dict]:
        self.calls.append(params)
        assert params["order"] == "bug_id DESC"
        fields = params["include_fields"].split(",")
        found = sorted(
            (b for b in self.bugs if self._matches(b, params)), key=lambda b: -b["id"]
        )
        return [{f: b.get(f) for f in fields} for b in found[: params["limit"]]]


@pytest.fixture
def corpus() -> list[dict]:
    return load("dupes_corpus.json")["bugs"]


# --- shared builders: the triaged bug, recorded actions and a fake session --- #

BUG = 9000001
DUPE_TARGET = 9000004
KNOWN_META = sorted(TEAM_META_BUGS)[0]
REACTION_FOOTER = COMMENT_FOOTER_SEPARATOR + runtime_bugzilla._COMMENT_FOOTER


def _recorder(
    state=None,
    bug=BUG,
    searchfox_rev=None,
    repo=Path(__file__).parent,
    meta_candidates=frozenset(),
    close_verdicts=None,
    commit_authors=frozenset(),
    investigations=None,
):
    """A recorder with the run's hooks installed.

    ``investigations`` defaults to one `bug_investigator` spawn, so only the tests of
    the early-ask check meet it.
    """
    # A comment-only test needs no real state; `require_state` always gives the agent one.
    state = state or TriageState(bug={}, now=NOW)
    rec = ActionsRecorder()
    install_hooks(
        rec,
        bug,
        state,
        searchfox_rev=searchfox_rev,
        repo=repo,
        meta_candidates=meta_candidates,
        close_verdicts=close_verdicts or CloseVerdicts(),
        commit_authors=commit_authors,
        investigations=(
            Investigations(spawns=1) if investigations is None else investigations
        ),
    )
    return rec


def _verdicts(*closes, verdict="confirm"):
    """A store with one ``verdict`` per ``(resolution, dupe_of)`` in ``closes``."""
    return CloseVerdicts(
        [CloseVerdict(r, d, verdict, "checked", ()) for r, d in closes]
    )


def _text(body: str) -> str:
    # What `bugzilla.add_comment` hands the hooks: the model's text, then the runtime's
    # reaction footer.
    return f"{body}{REACTION_FOOTER}"


SHORT_NOTE = _text("Short note.")


def _comment(rec, text=SHORT_NOTE, bug_id=BUG, **params):
    return rec.record(
        "bugzilla.add_comment",
        {"bug_id": bug_id, "text": text, "is_private": False, **params},
        reasoning="rule X",
    )


def _update(rec, changes, bug_id=BUG):
    return rec.record(
        "bugzilla.update_bug",
        {"bug_id": bug_id, "changes": changes},
        reasoning="rule Y",
    )


def _dupe_recorder(state, **kwargs):
    return _recorder(
        state, close_verdicts=_verdicts(("DUPLICATE", DUPE_TARGET)), **kwargs
    )


def _duplicate_of_target(rec):
    _comment(rec)
    _update(
        rec,
        {"status": "RESOLVED", "resolution": "DUPLICATE", "dupe_of": DUPE_TARGET},
    )


# Plain action dicts, as `ActionsRecorder.actions` holds them, for the post-run checks.
DUPE = 9000002
TARGET_SCOPE = TRIAGE_SCOPE[0]


def _target(**fields):
    """The `dupe_of` bug as the post-run read returns it."""
    bug = {
        "id": DUPE,
        "groups": [],
        "product": TARGET_SCOPE.product,
        "component": TARGET_SCOPE.component,
        "status": "NEW",
        "resolution": "",
        "keywords": [],
        "summary": "Video stalls",
    }
    return {**bug, **fields}


def _actions(dupe_of=DUPE, meta_on=DUPE, regressed_by=None):
    """A comment and a DUPLICATE close, with the meta bug on ``meta_on``."""
    changes = {"status": "RESOLVED", "resolution": "DUPLICATE", "dupe_of": dupe_of}
    if regressed_by:
        changes["regressed_by"] = {"add": regressed_by}
    actions = [
        {
            "action_id": "c",
            "type": "bugzilla.add_comment",
            "params": {"bug_id": BUG, "text": "x"},
        },
        {
            "action_id": "u",
            "type": "bugzilla.update_bug",
            "params": {"bug_id": BUG, "changes": changes},
        },
    ]
    if meta_on is not None:
        actions.append(
            {
                "action_id": "m",
                "type": "bugzilla.update_bug",
                "params": {
                    "bug_id": meta_on,
                    "changes": {"blocks": {"add": [KNOWN_META]}},
                },
            }
        )
    return actions


PLAN = '```json\n{"decision": "analyzed", "issue_type": "playback-failure"}\n```'


def _result(text=PLAN, is_error=False, subtype="success"):
    return ResultMessage(
        subtype=subtype,
        duration_ms=1,
        duration_api_ms=1,
        is_error=is_error,
        num_turns=3,
        session_id="s",
        total_cost_usd=0.5,
        result=text,
    )


class FakeClient:
    """Stands in for ClaudeSDKClient: runs ``script`` on the query, then ends."""

    script = None
    before = ()
    result = None
    options = None
    prompt = None

    def __init__(self, options):
        FakeClient.options = options

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def query(self, prompt):
        FakeClient.prompt = prompt
        if FakeClient.script and inspect.isawaitable(done := FakeClient.script()):
            await done

    async def receive_response(self):
        for msg in FakeClient.before:
            yield msg
        yield FakeClient.result


@pytest.fixture
def fake_client(monkeypatch):
    monkeypatch.setattr(agent, "ClaudeSDKClient", FakeClient)
    monkeypatch.setattr(agent, "make_undumpable", lambda: None)
    FakeClient.script, FakeClient.result, FakeClient.options = None, _result(), None
    FakeClient.before = ()
    return FakeClient


@pytest.fixture
def verdict_stores(monkeypatch):
    """The close verdict store of each run, as the run binds it to its server."""
    stores = []
    real = closing.verdicts_server
    monkeypatch.setattr(
        closing, "verdicts_server", lambda store: stores.append(store) or real(store)
    )
    return stores


def _record_verdict(stores, resolution, dupe_of=None, verdict="confirm"):
    stores[-1].verdicts.append(
        CloseVerdict(resolution, dupe_of, verdict, "checked", ())
    )


async def _run(tmp_path, state, recorder=None, searchfox_rev=None, **kwargs):
    return await agent.run_gecko_triage(
        task="t",
        bugzilla_mcp_server={"type": "http", "url": ""},
        source_repo=tmp_path,
        toolkit_dir=tmp_path,
        bug=BUG,
        state=state,
        searchfox_rev=searchfox_rev,
        actions_recorder=recorder or ActionsRecorder(),
        **kwargs,
    )


def _fake_fetch(monkeypatch, found):
    """Replaces the post-run read with ``found``; the id lists it was asked for."""
    calls = []

    async def fetch(server, ids):
        calls.append(ids)
        return {i: found[i] for i in ids if i in found}

    monkeypatch.setattr(agent, "fetch_bugs", fetch)
    return calls


def full_prompt(state, known_docs=(), duplicate_candidates=None) -> str:
    """What the model reads at the start of a run: the system prompt, then the user one."""
    return "\n\n".join(
        (
            agent.load_system_prompt(),
            agent.render_user_prompt(BUG, "t", state, known_docs, duplicate_candidates),
        )
    )
