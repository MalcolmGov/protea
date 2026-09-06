from pathlib import Path

import pytest

from protea.config import load_config
from protea.economics import EconomicsConfig, evaluate, render_markdown

REPO = Path(__file__).resolve().parent.parent


def _cfg(**over) -> EconomicsConfig:
    base = load_config(REPO / "configs/economics/zara-v0.yaml", "economics").model_dump()
    base.update(over)
    return EconomicsConfig.model_validate(base)


def test_committed_model_is_honest_about_low_volume():
    report = evaluate(_cfg())
    assert report.frontier_cost_usd > 0
    assert report.protea_fixed_usd > report.frontier_cost_usd  # at forecast the GPU is under-used
    assert report.savings_usd < 0
    assert report.gpu_utilisation < 0.1
    assert 1.0 < report.breakeven_multiple_of_forecast < 3.0
    assert report.kill_signal is False
    md = render_markdown(report)
    assert "Break-even volume" in md
    assert "Verdict" in md


def test_volume_flips_the_verdict_and_kill_signal():
    cfg = _cfg()
    big = cfg.model_copy(
        update={"forecast": [f.model_copy(update={"requests_per_day": f.requests_per_day * 5}) for f in cfg.forecast]}
    )
    report = evaluate(big)
    assert report.savings_usd > 0
    assert report.verdict.startswith("self-hosting saves")
    assert report.breakeven_multiple_of_forecast < 1.0
    tiny = cfg.model_copy(
        update={"forecast": [f.model_copy(update={"requests_per_day": f.requests_per_day / 20}) for f in cfg.forecast]}
    )
    report = evaluate(tiny)
    assert report.kill_signal is True
    assert report.verdict.startswith("KILL SIGNAL")
    none = cfg.model_copy(update={"forecast": [f.model_copy(update={"protea_share": 0.0}) for f in cfg.forecast]})
    assert evaluate(none).breakeven_multiple_of_forecast is None
    assert evaluate(none).kill_signal is True


def test_frontier_shares_must_sum_to_one():
    half = load_config(REPO / "configs/economics/zara-v0.yaml", "economics").model_dump()
    half["frontier"] = [{"model": "a", "input_usd_per_m": 1, "output_usd_per_m": 2, "share": 0.5}]
    with pytest.raises(ValueError, match="shares must sum"):
        EconomicsConfig.model_validate(half)
