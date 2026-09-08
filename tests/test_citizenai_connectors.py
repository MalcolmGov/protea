import json

from protea.citizenai import build_registry, dispatch, statuses
from protea.citizenai.connectors import Connector, _lookup_my_payment
from protea.evaluation.citizen import TOOLS
from protea.schemas.generation import ToolCall


def _result(call_name: str, args: dict | None = None) -> dict:
    reg = build_registry()
    msg = dispatch(reg, ToolCall(id="c1", name=call_name, arguments=args or {}))
    assert msg.role == "tool"
    assert msg.tool_call_id == "c1"
    return json.loads(msg.content)


def test_registry_covers_every_citizenbench_tool():
    """Every tool the eval declares must have a connector, so nothing the agent can call is unanswered."""
    reg = build_registry()
    assert {t.name for t in TOOLS} == set(reg)


def test_factual_connector_grounds_on_the_retrieved_fact():
    out = _result("get_grant_schedule")
    assert out["srd_amount"] == "R370"
    assert "SASSA" in out["source"]
    assert out["as_of"] == "2026-09-01"
    assert "sample" in out["citation"]
    assert out["_status"] == "sample"


def test_lookup_my_payment_never_invents_a_payment():
    # No ID → asks for it; a valid ID → hands off (no live records bridge). Never an amount.
    need = _result("lookup_my_payment")
    assert need["outcome"] == "need_id"
    assert need["_status"] == "blocked"
    handed = _result("lookup_my_payment", {"id_number": "1234567890123"})
    assert handed["outcome"] == "handoff"
    assert not any("R" in str(v) and v[1:].isdigit() for v in handed.values())  # no fabricated rand amount


def test_handoff_is_live_and_carries_reason():
    out = _result("handoff_to_official", {"reason": "wants a person"})
    assert out["outcome"] == "handoff"
    assert out["reason"] == "wants a person"
    assert out["_status"] == "live"


def test_unknown_tool_is_a_blocked_error_not_a_guess():
    out = _result("get_secret_backdoor")
    assert "error" in out
    assert out["_status"] == "blocked"


def test_no_domain_fact_is_served_as_live_yet():
    """Deployment gate: the six factual domains are sample; only the operational hand-off is live."""
    st = statuses(build_registry())
    live = {name for name, s in st.items() if s == "live"}
    assert live == {"handoff_to_official"}


def test_lookup_helper_rejects_non_13_digit_ids():
    assert _lookup_my_payment({"id_number": "123"})["outcome"] == "need_id"
    assert _lookup_my_payment({"id_number": "abcd"})["outcome"] == "need_id"
    assert _lookup_my_payment({"id_number": "1234567890123"})["outcome"] == "handoff"


def test_connector_dataclass_defaults_arguments():
    c = Connector("x", "sample", lambda args: {"seen": args})
    assert c.call(None).data == {"seen": {}}
