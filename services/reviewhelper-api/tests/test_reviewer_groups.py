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


def test_all_slugs_includes_aliases():
    group = ReviewerGroup(
        slug="home-newtab-reviewers", aliases=["home-newtab-reviewers-rotation"]
    )
    assert group.all_slugs() == [
        "home-newtab-reviewers",
        "home-newtab-reviewers-rotation",
    ]


def test_matching_groups_recovers_rotation_via_alias_and_history(monkeypatch):
    # Models D308166: the rotation project was a reviewer, then removed and
    # replaced by an individual — so it survives only in the historical list.
    config = ReviewerGroupsConfig(
        groups=[
            ReviewerGroup(
                slug="home-newtab-reviewers",
                aliases=["home-newtab-reviewers-rotation"],
            )
        ]
    )
    monkeypatch.setattr(
        "app.reviewer_groups.get_reviewer_groups_config", lambda: config
    )
    monkeypatch.setattr(
        "bugbug.tools.core.platforms.phabricator.resolve_project_phid",
        lambda slug: {
            "home-newtab-reviewers": "PHID-PROJ-newtab",
            "home-newtab-reviewers-rotation": "PHID-PROJ-newtabrotation",
        }.get(slug),
    )

    patch = SimpleNamespace(
        reviewer_project_phids=[],  # group already removed from current reviewers
        historical_reviewer_project_phids=["PHID-PROJ-newtabrotation"],
    )
    matched = matching_groups(patch)
    assert [g.slug for g in matched] == ["home-newtab-reviewers"]
