from protea.data_pipeline.scanners.brand import ScrubRules
from protea.data_pipeline.scanners.contamination import detect_contamination
from protea.data_pipeline.scanners.pii import redact, scan_pii
from protea.data_pipeline.scanners.secrets import scan_secrets


def test_secret_rules_catch_platform_and_vendor_keys():
    text = "keys: pak_ABCDEFGHIJKLMNOPQRSTUV aps_ABCDEFGHIJKLMNOPQRSTUV mia_pk_49631706fe13c02f1036fc30 sk-ant-api03-ABCDEFGHIJKLMNOPQRSTUVWXYZ012345"
    rules = {h.rule for h in scan_secrets(text)}
    assert {"zara_partner_key", "zara_session", "embed_key", "anthropic_key"} <= rules
    assert all("ABCDEFGHIJKLMNOPQRSTUV" not in h.preview for h in scan_secrets(text))


def test_secret_rules_ignore_ordinary_text():
    assert scan_secrets("Order 4821 is out for delivery. Contact the desk on 071 234 5678.") == []


def test_pii_detects_sa_id_phone_email_and_skips_placeholders():
    kinds = {
        h.kind
        for h in scan_pii(
            "ID 8001015009087, call 082 555 1234 or +27 71 234 5678, mail jo@corp.co.za or help@example.com"
        )
    }
    assert kinds == {"sa_id", "phone", "email"}
    assert scan_pii("Invalid id 8001015009088 and support@example.com") == []


def test_redaction_is_deterministic_and_format_preserving():
    text = "Call 082 555 1234 or mail jo@corp.co.za. ID 8001015009087."
    out1, counts = redact(text, "synthetic")
    out2, _ = redact(text, "synthetic")
    assert out1 == out2
    assert counts == {"phone": 1, "email": 1, "sa_id": 1}
    assert "082 555 1234" not in out1
    assert "+27 60 555 " in out1
    assert "@example.com" in out1
    assert "[SA_ID]" in out1
    placeholder, _ = redact(text, "placeholder")
    assert "[PHONE]" in placeholder
    assert "[EMAIL]" in placeholder


def test_brand_and_infra_scrub():
    rules = ScrubRules.load()
    out, n = rules.apply("Built by MyInstantAI (MIAI) on 46.224.42.163 under /opt/aria; see app.myinstantai.com")
    assert "MyInstantAI" not in out
    assert "MIAI" not in out
    assert "[IP]" in out
    assert "/opt/aria" not in out
    assert n >= 4


def test_contamination_detects_injected_lines_but_not_grounded_facts():
    evals = [
        {"id": "a", "expect": {"says_any": ["please check what is available", "we will check availability first"]}},
        {"id": "b", "expect": {"says_any": ["R60"]}},
    ]
    clean = "Delivery R60. Free over R750."
    assert detect_contamination(clean, evals).evals_contaminated == 0
    dirty = clean + "\nplease check what is available before booking; we will check availability first\n"
    rep = detect_contamination(dirty, evals)
    assert rep.contaminated_eval_ids == ["a"]
    assert rep.suspicious_lines == 1
