from __future__ import annotations

from performance_tools.blind import BlindClient, find_cutoff, keep_comment, roll_back

CALC = "The [Performance Impact Calculator](https://mozilla.github.io/perf-triage/calculator.html?x) ..."


def c(time, text="x"):
    return {"creation_time": time, "text": text}


def test_cutoff_is_the_first_impact_change():
    history = [
        {"when": "2025-02-01T00:00:00Z", "changes": [{"field_name": "cc"}]},
        {
            "when": "2025-03-01T00:00:00Z",
            "changes": [{"field_name": "cf_performance_impact"}],
        },
    ]
    assert find_cutoff(history, [c("2025-01-01T00:00:00Z")]) == "2025-03-01T00:00:00Z"


def test_an_earlier_calculator_comment_moves_the_cutoff():
    history = [
        {
            "when": "2025-03-01T00:00:00Z",
            "changes": [{"field_name": "cf_performance_impact"}],
        }
    ]
    comments = [c("2025-01-01T00:00:00Z"), c("2025-02-01T00:00:00Z", CALC)]
    assert find_cutoff(history, comments) == "2025-02-01T00:00:00Z"


def test_roll_back_undoes_scalar_and_list_changes_after_the_cutoff():
    bug = {
        "cf_performance_impact": "high",
        "component": "Graphics: WebRender",
        "keywords": ["perf:responsiveness", "regression"],
    }
    history = [
        {
            "when": "2025-01-05T00:00:00Z",
            "changes": [
                {"field_name": "keywords", "added": "regression", "removed": ""}
            ],
        },
        {
            "when": "2025-03-01T00:00:00Z",
            "changes": [
                {
                    "field_name": "cf_performance_impact",
                    "added": "high",
                    "removed": "?",
                },
                {
                    "field_name": "keywords",
                    "added": "perf:responsiveness",
                    "removed": "",
                },
                {
                    "field_name": "component",
                    "added": "Graphics: WebRender",
                    "removed": "Performance",
                },
            ],
        },
    ]
    out = roll_back(bug, history, "2025-03-01T00:00:00Z")
    assert out == {
        "cf_performance_impact": "?",
        "component": "Performance",
        "keywords": ["regression"],
    }


def test_calculator_comments_and_later_comments_are_dropped_but_the_report_kept():
    cutoff = "2025-03-01T00:00:00Z"
    assert keep_comment(c("2025-01-01T00:00:00Z", CALC), cutoff, 0)
    assert not keep_comment(c("2025-02-01T00:00:00Z", CALC), cutoff, 1)
    assert not keep_comment(c("2025-03-01T00:00:00Z"), cutoff, 1)


class FakeClient:
    def __init__(self):
        self.bugs = {
            1: {
                "id": 1,
                "creation_time": "2025-01-01T00:00:00Z",
                "cf_performance_impact": "high",
            },
            2: {
                "id": 2,
                "creation_time": "2025-04-01T00:00:00Z",
                "cf_performance_impact": "---",
            },
        }

    def request(self, path, params=None):
        if path == "bug/1/history":
            return {
                "bugs": [
                    {
                        "history": [
                            {
                                "when": "2025-03-01T00:00:00Z",
                                "changes": [
                                    {
                                        "field_name": "cf_performance_impact",
                                        "added": "high",
                                        "removed": "---",
                                    }
                                ],
                            }
                        ]
                    }
                ]
            }
        if path == "bug/2/history":
            return {"bugs": [{"history": []}]}
        if path == "bug/1/comment":
            return {
                "bugs": {
                    "1": {
                        "comments": [
                            c("2025-01-01T00:00:00Z"),
                            c("2025-03-02T00:00:00Z", CALC),
                        ]
                    }
                }
            }
        if path == "bug":
            ids = [int(i) for i in str(params["id"]).split(",")]
            return {"bugs": [dict(self.bugs[i]) for i in ids]}
        raise AssertionError(path)


def test_the_client_hides_the_rating_the_calculator_comment_and_later_bugs():
    blind = BlindClient(FakeClient(), 1)
    assert blind.request("bug", {"id": "1,2"})["bugs"] == [
        {
            "id": 1,
            "creation_time": "2025-01-01T00:00:00Z",
            "cf_performance_impact": "---",
        }
    ]
    assert len(blind.request("bug/1/comment")["bugs"]["1"]["comments"]) == 1
