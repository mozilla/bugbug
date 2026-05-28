from types import SimpleNamespace

from app.reviewer_groups import (
    Defaults,
    ReviewerGroup,
    ReviewerGroupsConfig,
    matching_groups,
)


def test_effective_thresholds_fall_back_to_defaults():
    defaults = Defaults(risk_threshold=3, complexity_threshold=4)
    group = ReviewerGroup(slug="g", risk_threshold=5)
    assert group.effective_risk_threshold(defaults) == 5
    assert group.effective_complexity_threshold(defaults) == 4


def test_by_slug():
    config = ReviewerGroupsConfig(
        groups=[ReviewerGroup(slug="a"), ReviewerGroup(slug="b")]
    )
    assert config.by_slug("b").slug == "b"
    assert config.by_slug("missing") is None


def test_matching_groups_filters_to_requested_projects(monkeypatch):
    config = ReviewerGroupsConfig(
        groups=[
            ReviewerGroup(slug="ip-protection-reviewers"),
            ReviewerGroup(slug="home-newtab-reviewers"),
        ]
    )
    monkeypatch.setattr(
        "app.reviewer_groups.get_reviewer_groups_config", lambda: config
    )

    phid_by_slug = {
        "ip-protection-reviewers": "PHID-PROJ-ip",
        "home-newtab-reviewers": "PHID-PROJ-newtab",
    }
    monkeypatch.setattr(
        "bugbug.tools.core.platforms.phabricator.resolve_project_phid",
        lambda slug: phid_by_slug.get(slug),
    )

    patch = SimpleNamespace(reviewer_project_phids=["PHID-PROJ-ip"])
    matched = matching_groups(patch)
    assert [g.slug for g in matched] == ["ip-protection-reviewers"]


def test_matching_groups_empty_when_no_reviewer_projects(monkeypatch):
    monkeypatch.setattr(
        "app.reviewer_groups.get_reviewer_groups_config",
        lambda: ReviewerGroupsConfig(groups=[ReviewerGroup(slug="a")]),
    )
    patch = SimpleNamespace(reviewer_project_phids=[])
    assert matching_groups(patch) == []
