"""The capability spec is the behavioural contract; the eval config is the instrument that measures it.

These tests keep the two from drifting apart. The spec may not name a capability ZaraBench cannot measure,
may not miss one it does, and its tiers must agree with the eval config's priority / frontier gate lists.
This is what lets `docs/capability-spec.yaml` stay a plain doc (not a schema-validated config kind) without
rotting — see ADR-014."""

from __future__ import annotations

from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
SPEC_PATH = REPO / "docs/capability-spec.yaml"
EVAL_PATH = REPO / "configs/evaluation/zarabench-0.1.yaml"

TIERS = {"frontier_gate", "priority", "guardrail", "supporting"}


def _spec() -> dict:
    return yaml.safe_load(SPEC_PATH.read_text(encoding="utf-8"))


def _eval() -> dict:
    return yaml.safe_load(EVAL_PATH.read_text(encoding="utf-8"))


def test_spec_is_well_formed():
    spec = _spec()
    for key in ("spec", "version", "family", "base_model_independent", "capabilities"):
        assert key in spec, f"missing top-level key {key!r}"
    assert spec["base_model_independent"] is True
    assert spec["capabilities"], "no capabilities declared"


def test_every_capability_is_complete():
    for cap in _spec()["capabilities"]:
        for key in ("id", "title", "tier", "statement", "why", "evidence", "release_rule", "gate_status"):
            assert cap.get(key), f"capability {cap.get('id')!r} missing/empty {key!r}"
        assert cap["tier"] in TIERS, f"{cap['id']}: unknown tier {cap['tier']!r}"
        ev = cap["evidence"]
        assert ev["suite"] == "zarabench", f"{cap['id']}: evidence.suite must be zarabench"
        assert ev["category"] == cap["id"], f"{cap['id']}: evidence.category must equal the capability id"
        assert ev.get("checks"), f"{cap['id']}: evidence.checks is empty"


def test_capabilities_match_eval_categories_exactly():
    """A bijection: no capability the eval can't measure, no measured category the contract ignores."""
    spec_ids = {c["id"] for c in _spec()["capabilities"]}
    eval_cats = {c["name"] for c in _eval()["categories"]}
    assert spec_ids == eval_cats, f"spec vs eval categories differ: {spec_ids ^ eval_cats}"


def test_evidence_suite_matches_eval_suite():
    suite = _eval()["suite"]
    for cap in _spec()["capabilities"]:
        assert cap["evidence"]["suite"] == suite


def test_frontier_gate_tier_matches_eval_config():
    spec = _spec()
    eval_cfg = _eval()
    frontier_from_spec = {c["id"] for c in spec["capabilities"] if c["tier"] == "frontier_gate"}
    assert frontier_from_spec == set(eval_cfg["frontier_gate_categories"])


def test_priority_tiers_cover_eval_priority_categories():
    """The eval's priority_categories must all be either frontier_gate or priority in the spec — those are
    the tiers held to a ">= base" (or stronger) rule; nothing weaker may claim a priority category."""
    spec = _spec()
    load_bearing = {c["id"] for c in spec["capabilities"] if c["tier"] in ("frontier_gate", "priority")}
    assert load_bearing == set(_eval()["priority_categories"])


def test_guardrails_are_not_graded_against_the_base():
    """Safety and truthfulness are absolute floors: the rule must say 'absolute floor' and must not use the
    positive base-comparison the priority tiers use ('>= frozen base'). The guardrail rules DO contain the
    negated phrase `never ">= base"`, which is the point — so the forbidden marker is the positive one."""
    for cap in _spec()["capabilities"]:
        if cap["tier"] == "guardrail":
            rule = cap["release_rule"].lower()
            assert "absolute floor" in rule, f"{cap['id']}: guardrail must state an absolute floor"
            assert ">= frozen base" not in rule, f"{cap['id']}: guardrail must not be graded against the base"
