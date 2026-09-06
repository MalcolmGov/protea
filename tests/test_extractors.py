from pathlib import Path

import pytest

from protea.data_pipeline.extractors.agent_packages import family_of, load_package_dir, load_package_json
from protea.data_pipeline.extractors.presets import load_presets
from protea.data_pipeline.extractors.python_literals import load_literal, registry_entries

FIX = Path(__file__).resolve().parent / "fixtures" / "sources"


def test_family_of_strips_market_prefix():
    assert family_of("africa-salon-booking") == ("salon-booking", "africa")
    assert family_of("order-desk") == ("order-desk", None)


def test_load_package_json_and_dir():
    pkg = load_package_json(
        FIX / "aria/data/agents/africa-salon-booking.agent.json", "data/agents/africa-salon-booking.agent.json"
    )
    assert pkg.family == "salon-booking"
    assert pkg.market == "africa"
    assert pkg.tool_names == {"list_services", "check_availability", "book_appointment", "handoff_to_human"}
    assert len(pkg.evals) == 5
    authored = load_package_dir(FIX / "miai/agents/order-desk/manifest.json", "agents/order-desk/manifest.json")
    assert authored.format == "miai.agent-package/v1"
    assert authored.family == "order-desk"
    assert len(authored.evals) == 3
    assert authored.tools[0]["name"] == "get_order_status"


def test_unsupported_format_rejected(tmp_path):
    p = tmp_path / "x.agent.json"
    p.write_text('{"format": "other/v9", "manifest": {"id": "x"}}')
    with pytest.raises(ValueError, match="unsupported package format"):
        load_package_json(p, "x.agent.json")


def test_presets_parse_generated_ts():
    presets = load_presets(FIX / "aria/agent_runtime/packages/presets/src/generated-presets.ts")
    assert presets["salon-booking"][1] == {"tool": "check_availability", "connector": "google_calendar"}
    assert len(presets) == 2


def test_registry_and_literals_without_import():
    entries = registry_entries(FIX / "aria/embed/connectors_v2.py", "CONNECTORS_CATALOG")
    ids = {e["id"] for e in entries}
    assert ids == {"google_calendar", "slack", "teams"}
    teams = next(e for e in entries if e["id"] == "teams")
    assert teams["scopes"] is None  # field(default_factory=...) is not a literal
    corpus = load_literal(FIX / "aria/tests/routing/corpus.py", "CORPUS")
    assert corpus["health"]["must_claim"][0] == "medicine price at dischem"
    flagship = load_literal(FIX / "aria/embed/flagship_agents.py", "FLAGSHIP_AGENTS")
    assert flagship["cfo"]["__type__"] == "AgentSpec"
    assert flagship["cfo"]["roi_model"]["payback_months"] == 0.5
    with pytest.raises(ValueError, match="no module-level assignment"):
        load_literal(FIX / "aria/tests/routing/corpus.py", "NOPE")


def test_pinned_commit_mismatch_is_an_error(tmp_path):
    import subprocess

    from protea.data_pipeline.sources import SourceSpec, resolve_commit

    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "f").write_text("x")
    subprocess.run(["git", "-C", str(tmp_path), "-c", "user.name=t", "-c", "user.email=t@t", "add", "f"], check=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "x"], check=True
    )
    spec = SourceSpec(name="s", repo="o/r", root=str(tmp_path), commit="deadbeef", extractors=[])
    with pytest.raises(ValueError, match="pinned commit"):
        resolve_commit(spec, tmp_path)
    ok = SourceSpec(name="s", repo="o/r", root=str(tmp_path), extractors=[])
    assert len(resolve_commit(ok, tmp_path)) == 40
    export = SourceSpec(name="e", repo="o/r", root=str(tmp_path / "nogit"), commit="export-1", extractors=[])
    (tmp_path / "nogit").mkdir()
    assert resolve_commit(export, tmp_path / "nogit") == "export-1"
