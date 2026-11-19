from unittest import mock

import requests
from autowebcompat_tools.inputs import BugDataInput, BugIdInput
from hackbot_agents.autowebcompat_etp import agent, mcp_servers
from hackbot_agents.autowebcompat_etp.result import (
    PNG_SIGNATURE,
    EtpInvestigationResult,
    ProposedShim,
)


def make_result(*proposals):
    return EtpInvestigationResult(
        summary="",
        failure_reason=None,
        broken_in_standard=False,
        broken_in_strict=True,
        broken_with_easyprivacy=None,
        broken_with_easylist=None,
        culprit_prefs=[],
        blocked_resources=[],
        proposed_exceptions=list(proposals),
        exception_verified=True,
        severity="S3",
        priority="P3",
        steps="",
    )


SITE_SCOPED = {
    "category": "baseline",
    "urlPattern": "*://*.licdn.com/*",
    "topLevelUrlPattern": "*://www.example.com/*",
    "classifierFeatures": ["tracking-protection"],
    "rationale": "",
}
GLOBAL = SITE_SCOPED | {"urlPattern": "*://cdn.widget.io/*", "topLevelUrlPattern": None}


def test_exception_records_shape():
    records = agent.exception_records(
        make_result(SITE_SCOPED, GLOBAL), BugIdInput(bug_id=123)
    )

    assert records == [
        {
            "bugIds": ["123"],
            "category": "baseline",
            "urlPattern": "*://*.licdn.com/*",
            "classifierFeatures": ["tracking-protection"],
            "topLevelUrlPattern": "*://www.example.com/*",
        },
        {
            "bugIds": ["123"],
            "category": "baseline",
            "urlPattern": "*://cdn.widget.io/*",
            "classifierFeatures": ["tracking-protection"],
        },
    ]


def test_exception_records_without_bug_id():
    records = agent.exception_records(make_result(GLOBAL), BugDataInput(bug_data="x"))
    assert records[0]["bugIds"] == []


def test_bug_tags():
    social = SITE_SCOPED | {
        "urlPattern": "*://platform.linkedin.com/*",
        "classifierFeatures": ["socialtracking-protection", "tracking-protection"],
    }
    assert agent.bug_tags(make_result(SITE_SCOPED, social)) == (
        ["[privacy-team:diagnosed]", "[exception-baseline]"],
        [
            "trackers-blocked:*.licdn.com,platform.linkedin.com",
            "classifier-features:tracking-protection,socialtracking-protection",
        ],
    )


def test_bug_tags_mixed_categories(caplog):
    convenience = GLOBAL | {"category": "convenience"}
    whiteboard, _ = agent.bug_tags(make_result(SITE_SCOPED, convenience))
    assert whiteboard == [
        "[privacy-team:diagnosed]",
        "[exception-baseline]",
        "[exception-convenience]",
    ]
    assert "mix baseline and convenience" in caplog.text


def test_bug_tags_without_exceptions():
    result = make_result(GLOBAL).model_copy(
        update={"failure_reason": "not_reproducible", "proposed_exceptions": []}
    )
    assert agent.bug_tags(result) == ([], [])


def test_publish_screenshots(tmp_path):
    png = tmp_path / "strict.png"
    png.write_bytes(PNG_SIGNATURE)
    result = make_result(GLOBAL).model_copy(
        update={
            "screenshot_standard": png,
            "screenshot_strict": png,
            "screenshot_easyprivacy": png,
            "screenshot_easylist": png,
        }
    )
    publish_file = mock.Mock(side_effect=lambda key, path, _: f"https://x/{key}")

    urls = agent.publish_screenshots(result, publish_file)

    assert urls == {
        "standard": "https://x/screenshot-standard.png",
        "strict": "https://x/screenshot-strict.png",
        "easyprivacy": "https://x/screenshot-easyprivacy.png",
        "easylist": "https://x/screenshot-easylist.png",
    }
    publish_file.assert_any_call("screenshot-strict.png", png, "image/png")


def test_publish_content_blocking_log(tmp_path):
    path = tmp_path / agent.CONTENT_BLOCKING_LOG_FILE
    publish_file = mock.Mock(side_effect=lambda key, path, _: f"https://x/{key}")

    assert agent.publish_content_blocking_log(path, publish_file) is None
    publish_file.assert_not_called()

    path.write_text('{"strict": []}')
    assert (
        agent.publish_content_blocking_log(path, publish_file)
        == "https://x/content-blocking-log.json"
    )
    publish_file.assert_called_once_with(
        "content-blocking-log.json", path, "application/json"
    )


def test_firefox_devtools_server_passes_log_file(tmp_path, monkeypatch):
    monkeypatch.setattr(mcp_servers, "resolve_bin", lambda name: name)
    log = tmp_path / "log.json"

    assert (
        "ETP_CONTENT_BLOCKING_LOG_FILE"
        not in (mcp_servers.build_firefox_devtools_server(tmp_path)["env"])
    )
    server = mcp_servers.build_firefox_devtools_server(
        tmp_path, content_blocking_log_file=log
    )
    assert server["env"]["ETP_CONTENT_BLOCKING_LOG_FILE"] == str(log)


def test_firefox_devtools_server_disables_exceptions(tmp_path, monkeypatch):
    monkeypatch.setattr(mcp_servers, "resolve_bin", lambda name: name)
    prefs = [f"{pref}=false" for pref in mcp_servers.ALLOW_LIST_PREFS]

    server = mcp_servers.build_firefox_devtools_server(tmp_path)
    assert not set(prefs) & set(server["args"])
    assert "ETP_DISABLE_EXCEPTIONS" not in server["env"]

    server = mcp_servers.build_firefox_devtools_server(
        tmp_path, disable_exceptions=True
    )
    assert set(prefs) <= set(server["args"])
    assert server["env"]["ETP_DISABLE_EXCEPTIONS"] == "1"


def test_retest_mode_disables_exceptions_and_says_so(tmp_path, monkeypatch):
    build = mock.Mock(return_value={})
    monkeypatch.setattr(agent, "build_firefox_devtools_server", build)
    monkeypatch.setattr(agent, "build_exceptions_server", mock.Mock())
    monkeypatch.setattr(agent, "build_tracker_lists_server", mock.Mock())

    def investigation(**kwargs):
        return agent.EtpInvestigation(
            mock.MagicMock(),
            mock.Mock(),
            tmp_path,
            BugIdInput(bug_id=1),
            {},
            tmp_path,
            **kwargs,
        )

    assert agent.RETEST_NOTE not in investigation().user_prompt()
    assert build.call_args.kwargs["disable_exceptions"] is False

    assert agent.RETEST_NOTE in investigation(mode="retest").user_prompt()
    assert build.call_args.kwargs["disable_exceptions"] is True


def test_final_result_keeps_counts_without_paths(tmp_path):
    png = tmp_path / "strict.png"
    png.write_bytes(PNG_SIGNATURE)
    result = EtpInvestigationResult.model_validate(
        make_result(GLOBAL).model_dump()
        | {"screenshot_strict": png, "blocked_trackers": {"strict": 38}}
    )

    # As run_autowebcompat_etp builds it, with the paths replaced by URLs.
    final = agent.AutowebcompatEtpResult(
        **result.model_dump(exclude=set(agent.SCREENSHOT_FIELDS.values())),
        exception_records=[],
        whiteboard_tags=[],
        user_story_lines=[],
        existing_exceptions=None,
        existing_shims=None,
        screenshot_urls={"strict": "https://x/screenshot-strict.png"},
    )

    assert final.blocked_trackers.strict == 38


SHIMS_JS = """
const AVAILABLE_SHIMS = [
  {
    id: "InstagramEmbed",
    name: "Instagram embed placeholder",
    bug: "1892173",
    matches: ["https://www.instagram.com/embed.js"],
    unblocksOnOptIn: [
      "*://www.instagram.com/*",
      "*://*.fbcdn.net/*", // covers:
      // "*://scontent.cdninstagram.com/*",
    ],
  },
  {
    id: "AdNexusAST",
    name: "AdNexus AST",
    bug: "1734130",
    matches: ["*://*.adnxs.com/*/ast.js*"],
  },
];
"""


def test_parse_shims():
    shims = agent.parse_shims(SHIMS_JS)

    assert [shim["id"] for shim in shims] == ["InstagramEmbed", "AdNexusAST"]
    assert shims[0] == {
        "id": "InstagramEmbed",
        "name": "Instagram embed placeholder",
        "bug": "1892173",
        # The commented-out pattern is not picked up.
        "patterns": [
            "https://www.instagram.com/embed.js",
            "*://www.instagram.com/*",
            "*://*.fbcdn.net/*",
        ],
    }


def make_shim(**overrides):
    return ProposedShim.model_validate(
        {
            "kind": "opt_in_placeholder",
            "name": "Instagram embed placeholder",
            "matches": ["https://www.instagram.com/embed.js"],
            "unblocksOnOptIn": ["*://www.instagram.com/*"],
            "behavior": "",
            "existing_shim_id": "InstagramEmbed",
            "rationale": "",
        }
        | overrides
    )


def _find_shims(shim):
    response = mock.Mock(text=SHIMS_JS)
    with mock.patch.object(agent.requests, "get", return_value=response):
        return agent.find_existing_shims(shim)


def test_finds_existing_shim_by_host():
    assert _find_shims(make_shim()) == [
        {
            "id": "InstagramEmbed",
            "name": "Instagram embed placeholder",
            "bug": "1892173",
            "patterns": [
                "https://www.instagram.com/embed.js",
                "*://www.instagram.com/*",
            ],
        }
    ]


def test_new_provider_has_no_existing_shim():
    shim = make_shim(
        matches=["*://platform.linkedin.com/in.js*"],
        unblocksOnOptIn=["*://www.linkedin.com/*"],
        existing_shim_id=None,
    )
    assert _find_shims(shim) == []


def test_warns_when_agent_missed_an_existing_shim(caplog):
    _find_shims(make_shim(existing_shim_id=None))
    assert "InstagramEmbed already cover" in caplog.text


def test_no_shim_skips_the_fetch():
    with mock.patch.object(agent.requests, "get") as get:
        assert agent.find_existing_shims(None) == []
    get.assert_not_called()


def test_shims_fetch_failure_returns_none():
    with mock.patch.object(
        agent.requests, "get", side_effect=requests.ConnectionError()
    ):
        assert agent.find_existing_shims(make_shim()) is None
