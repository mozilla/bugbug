"""Scope evaluation, especially the ways it must refuse."""

from bugzilla_proxy.scope import (
    AUTH_FIELDS,
    DEFAULT_METADATA_FIELDS,
    Anchor,
    Grant,
    Scope,
)


def public_bug(**overrides) -> dict:
    bug = {
        "id": 100,
        "groups": [],
        "product": "Core",
        "component": "DOM",
        "status": "NEW",
        "resolution": "",
        "keywords": ["regression"],
        "whiteboard": "[fidefe-triage]",
        "blocks": [900],
        "creation_time": "2024-03-01T10:00:00Z",
        "summary": "A public bug",
    }
    bug.update(overrides)
    return bug


def grant(**overrides) -> Grant:
    defaults = {
        "tier": "full",
        "anchor": Anchor(),
        "endpoints": ("bug",),
    }
    defaults.update(overrides)
    return Grant(**defaults)


class TestGroupCeiling:
    def test_public_bug_is_admitted_by_a_public_grant(self):
        assert grant().permits(public_bug())

    def test_private_bug_is_refused_by_a_public_grant(self):
        assert not grant().permits(public_bug(groups=["core-security"]))

    def test_private_bug_is_admitted_when_its_group_is_granted(self):
        g = grant(groups=frozenset({"core-security"}))
        assert g.permits(public_bug(groups=["core-security"]))

    def test_a_single_ungranted_group_is_enough_to_refuse(self):
        g = grant(groups=frozenset({"core-security"}))
        assert not g.permits(public_bug(groups=["core-security", "partner-nda"]))

    def test_group_grant_still_admits_public_bugs(self):
        g = grant(groups=frozenset({"core-security"}))
        assert g.permits(public_bug(groups=[]))

    def test_a_bug_without_a_groups_field_is_refused(self):
        """Missing means unprovable, and unprovable must not mean public."""
        bug = public_bug()
        del bug["groups"]
        assert not grant(groups=frozenset({"core-security"})).permits(bug)


class TestAnchorRules:
    def test_every_configured_rule_must_hold(self):
        g = grant(anchor=Anchor(product=frozenset({"Core"}), status=frozenset({"NEW"})))
        assert g.permits(public_bug())
        assert not g.permits(public_bug(status="RESOLVED"))

    def test_static_bugs_restricts_to_listed_ids(self):
        g = grant(anchor=Anchor(static_bugs=frozenset({100})))
        assert g.permits(public_bug(id=100))
        assert not g.permits(public_bug(id=101))

    def test_keywords_match_on_intersection(self):
        g = grant(anchor=Anchor(keywords=frozenset({"regression"})))
        assert g.permits(public_bug())
        assert not g.permits(public_bug(keywords=["perf"]))

    def test_whiteboard_matches_on_substring(self):
        g = grant(anchor=Anchor(whiteboard=("[fidefe-triage]",)))
        assert g.permits(public_bug())
        assert not g.permits(public_bug(whiteboard="[other]"))

    def test_created_after_excludes_older_bugs(self):
        g = grant(anchor=Anchor(created_after="2024-01-01T00:00:00Z"))
        assert g.permits(public_bug())
        assert not g.permits(public_bug(creation_time="2019-06-01T10:00:00Z"))

    def test_created_after_refuses_a_bug_with_no_creation_time(self):
        g = grant(anchor=Anchor(created_after="2024-01-01T00:00:00Z"))
        bug = public_bug()
        del bug["creation_time"]
        assert not g.permits(bug)

    def test_an_empty_anchor_matches_anything_public(self):
        assert grant().permits(public_bug(product="Firefox", status="RESOLVED"))

    def test_structural_rules_are_distinguished_from_narrowing_ones(self):
        assert Anchor(product=frozenset({"Core"})).has_structural_rule()
        assert not Anchor(keywords=frozenset({"sec-high"})).has_structural_rule()
        assert not Anchor(whiteboard=("[x]",)).has_structural_rule()
        assert not Anchor(blocks=frozenset({12})).has_structural_rule()


class TestEndpointPatterns:
    def test_exact_and_wildcard_segments(self):
        g = grant(endpoints=("bug", "bug/*/comment"))
        assert g.allows_endpoint("bug")
        assert g.allows_endpoint("bug/123/comment")
        assert not g.allows_endpoint("bug/123/attachment")

    def test_a_wildcard_covers_exactly_one_segment(self):
        g = grant(endpoints=("bug/*/comment",))
        assert not g.allows_endpoint("bug/comment")
        assert not g.allows_endpoint("bug/1/2/comment")


class TestProjection:
    def test_metadata_tier_drops_everything_outside_its_field_set(self):
        g = grant(tier="metadata")
        projected = g.project(public_bug())
        assert set(projected) <= DEFAULT_METADATA_FIELDS
        assert "whiteboard" not in projected
        assert "groups" not in projected
        assert projected["summary"] == "A public bug"

    def test_full_tier_keeps_what_upstream_sent(self):
        projected = grant().project(public_bug())
        assert projected["whiteboard"] == "[fidefe-triage]"

    def test_an_explicit_field_list_wins_over_the_tier_default(self):
        g = grant(tier="metadata", fields=frozenset({"id", "summary"}))
        assert set(g.project(public_bug())) == {"id", "summary"}

    def test_the_caller_can_narrow_but_not_widen(self):
        g = grant(tier="metadata")
        projected = g.project(public_bug(), requested=frozenset({"id", "whiteboard"}))
        assert projected == {"id": 100}


class TestScopeSelection:
    def test_the_highest_matching_tier_wins(self):
        scope = Scope(
            run_id="r",
            agent="a",
            jti="j",
            grants=(
                grant(tier="metadata", endpoints=("bug",)),
                grant(
                    tier="full",
                    anchor=Anchor(static_bugs=frozenset({100})),
                    endpoints=("bug",),
                ),
            ),
        )
        assert scope.grant_for(public_bug(id=100)).tier == "full"
        assert scope.grant_for(public_bug(id=101)).tier == "metadata"

    def test_endpoint_selection_ignores_grants_that_do_not_expose_it(self):
        scope = Scope(
            run_id="r",
            agent="a",
            jti="j",
            grants=(
                grant(tier="metadata", endpoints=("bug",)),
                grant(
                    tier="full",
                    anchor=Anchor(static_bugs=frozenset({100})),
                    endpoints=("bug", "bug/*/comment"),
                ),
            ),
        )
        bug = public_bug(id=101)
        assert scope.grant_for(bug) is not None
        assert scope.grant_for_endpoint(bug, "bug/*/comment") is None

    def test_a_scope_is_private_when_any_grant_is(self):
        scope = Scope(
            run_id="r",
            agent="a",
            jti="j",
            grants=(grant(), grant(groups=frozenset({"core-security"}))),
        )
        assert scope.is_private


class TestUpstreamFields:
    def test_auth_fields_are_always_requested(self):
        scope = Scope(run_id="r", agent="a", jti="j", grants=(grant(tier="metadata"),))
        assert AUTH_FIELDS <= scope.upstream_fields(frozenset({"id", "summary"}))

    def test_a_full_grant_asks_for_the_default_set_plus_auth_fields(self):
        scope = Scope(run_id="r", agent="a", jti="j", grants=(grant(),))
        fields = scope.upstream_fields(None)
        assert "_default" in fields
        assert AUTH_FIELDS <= fields

    def test_metadata_only_scopes_ask_for_no_more_than_they_can_show(self):
        scope = Scope(run_id="r", agent="a", jti="j", grants=(grant(tier="metadata"),))
        assert scope.upstream_fields(None) == DEFAULT_METADATA_FIELDS | AUTH_FIELDS
