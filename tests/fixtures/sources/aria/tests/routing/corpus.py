"""Golden routing corpus."""
CORPUS: dict[str, dict[str, list[str]]] = {
    "biz_setup": {"must_claim": ["register my business", "set up a new business"], "must_not_claim": ["register my shop"]},
    "health": {"must_claim": ["medicine price at dischem", "remind me to take my meds", "generic for panado"], "must_not_claim": []},
}
