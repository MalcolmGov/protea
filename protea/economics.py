"""Economic model (spec §69, strategy review A1): does self-hosting Protea beat frontier tokens at forecast volume?

Inputs are a config (`configs/economics/*.yaml`, kind `economics`): the request forecast per task type with average
token counts, the frontier price mix it would otherwise pay, the serving GPU (price and measured or assumed
tokens/s), training cost per iteration, engineering and operations overhead. Outputs: monthly cost on each path,
break-even volume and GPU utilisation, the margin at forecast, and the kill signal the strategy review defines
("break-even utilisation exceeds forecast volume by a wide margin"). Prices are inputs, never fetched."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, model_validator

HOURS_PER_MONTH = 730.0


class TaskForecast(BaseModel):
    task_type: str
    requests_per_day: float = Field(ge=0)
    input_tokens: int = Field(ge=1)
    output_tokens: int = Field(ge=1)
    protea_share: float = Field(default=1.0, ge=0.0, le=1.0)  # share the router could send to Protea once eligible


class FrontierPrice(BaseModel):
    model: str
    input_usd_per_m: float = Field(ge=0)
    output_usd_per_m: float = Field(ge=0)
    share: float = Field(default=1.0, gt=0, le=1.0)  # share of frontier traffic on this model


class ServingGpu(BaseModel):
    name: str
    provider: str = "generic"
    usd_per_hour: float = Field(gt=0)
    count: int = Field(default=1, ge=1)
    output_tokens_per_second: float = Field(gt=0)  # sustained generation throughput per GPU at target batch size
    target_utilisation: float = Field(default=0.6, gt=0, le=1.0)  # headroom for peaks; break-even is reported at 100%


class Overheads(BaseModel):
    training_usd_per_iteration: float = Field(default=0.0, ge=0)
    training_iterations_per_month: float = Field(default=0.0, ge=0)
    engineering_hours_per_month: float = Field(default=0.0, ge=0)
    engineering_usd_per_hour: float = Field(default=0.0, ge=0)
    operations_usd_per_month: float = Field(default=0.0, ge=0)  # monitoring, storage, egress, on-call
    frontier_fallback_share: float = Field(default=0.1, ge=0.0, le=1.0)  # Protea traffic that still falls back


class EconomicsConfig(BaseModel):
    name: str
    currency_note: str = "USD; convert with the finance rate of the month"
    forecast: list[TaskForecast]
    frontier: list[FrontierPrice]
    gpu: ServingGpu
    overheads: Overheads = Field(default_factory=Overheads)
    kill_breakeven_multiple: float = Field(default=3.0, gt=1.0)  # break-even volume > forecast × this ⇒ kill signal

    @model_validator(mode="after")
    def _shares(self) -> EconomicsConfig:
        total = sum(f.share for f in self.frontier)
        if abs(total - 1.0) > 1e-6:
            raise ValueError(f"frontier shares must sum to 1.0 (got {total:.3f})")
        return self


class EconomicsReport(BaseModel):
    name: str
    requests_per_month: float
    protea_requests_per_month: float
    tokens_per_month: dict[str, float]
    frontier_cost_usd: float  # everything on frontier models
    protea_fixed_usd: float  # GPU + training + engineering + operations, independent of volume
    protea_variable_usd: float  # frontier fallback share priced at frontier rates
    protea_cost_usd: float
    savings_usd: float
    savings_share: float
    gpu_hours_needed: float
    gpu_utilisation: float  # at forecast, of the configured GPUs
    capacity_requests_per_month: float
    breakeven_requests_per_month: float
    breakeven_utilisation: float
    breakeven_multiple_of_forecast: float | None
    kill_signal: bool
    verdict: str
    assumptions: list[str] = Field(default_factory=list)


def _blended(frontier: list[FrontierPrice]) -> tuple[float, float]:
    return (
        sum(p.input_usd_per_m * p.share for p in frontier),
        sum(p.output_usd_per_m * p.share for p in frontier),
    )


def evaluate(cfg: EconomicsConfig) -> EconomicsReport:
    days = HOURS_PER_MONTH / 24
    req_month = sum(f.requests_per_day for f in cfg.forecast) * days
    protea_req = sum(f.requests_per_day * f.protea_share for f in cfg.forecast) * days
    in_tokens = sum(f.requests_per_day * f.input_tokens for f in cfg.forecast) * days
    out_tokens = sum(f.requests_per_day * f.output_tokens for f in cfg.forecast) * days
    protea_in = sum(f.requests_per_day * f.protea_share * f.input_tokens for f in cfg.forecast) * days
    protea_out = sum(f.requests_per_day * f.protea_share * f.output_tokens for f in cfg.forecast) * days
    p_in, p_out = _blended(cfg.frontier)
    frontier_cost = (in_tokens * p_in + out_tokens * p_out) / 1e6

    ov = cfg.overheads
    gpu_month = cfg.gpu.usd_per_hour * cfg.gpu.count * HOURS_PER_MONTH
    fixed = gpu_month + ov.training_usd_per_iteration * ov.training_iterations_per_month
    fixed += ov.engineering_hours_per_month * ov.engineering_usd_per_hour + ov.operations_usd_per_month
    # traffic that leaves Protea again (fallback) is paid at frontier rates; the rest of the non-Protea share too
    non_protea_cost = ((in_tokens - protea_in) * p_in + (out_tokens - protea_out) * p_out) / 1e6
    fallback_cost = ov.frontier_fallback_share * (protea_in * p_in + protea_out * p_out) / 1e6
    variable = non_protea_cost + fallback_cost
    protea_cost = fixed + variable

    per_gpu_month_tokens = cfg.gpu.output_tokens_per_second * 3600 * HOURS_PER_MONTH
    capacity_tokens = per_gpu_month_tokens * cfg.gpu.count
    gpu_hours = protea_out / (cfg.gpu.output_tokens_per_second * 3600) if cfg.gpu.output_tokens_per_second else 0.0
    utilisation = protea_out / capacity_tokens if capacity_tokens else 0.0
    avg_out = protea_out / protea_req if protea_req else 1.0
    capacity_requests = capacity_tokens * cfg.gpu.target_utilisation / avg_out if avg_out else 0.0

    # break-even: fixed cost equals the frontier spend Protea displaces (net of fallback) at volume V
    avg_in = protea_in / protea_req if protea_req else 1.0
    displaced_per_request = (avg_in * p_in + avg_out * p_out) / 1e6 * (1.0 - ov.frontier_fallback_share)
    breakeven_req = fixed / displaced_per_request if displaced_per_request > 0 else float("inf")
    breakeven_util = (breakeven_req * avg_out) / capacity_tokens if capacity_tokens else float("inf")
    multiple = breakeven_req / protea_req if protea_req else None
    savings = frontier_cost - protea_cost
    kill = (
        multiple is None or multiple > cfg.kill_breakeven_multiple or breakeven_util > 1.0 * cfg.kill_breakeven_multiple
    )

    if kill:
        verdict = (
            f"KILL SIGNAL: break-even needs {breakeven_req:,.0f} Protea requests/month ({multiple:.1f}× the forecast)"
            if multiple is not None
            else "KILL SIGNAL: no Protea traffic forecast"
        ) + "; stay on frontier models behind the router and revisit when volume grows"
    elif savings > 0:
        verdict = f"self-hosting saves {savings:,.0f} USD/month ({savings / frontier_cost:.0%}) at {utilisation:.0%} GPU utilisation"
    else:
        verdict = (
            f"self-hosting costs {-savings:,.0f} USD/month more at forecast; "
            f"break-even at {breakeven_req:,.0f} requests/month ({multiple:.1f}× forecast)"
        )

    return EconomicsReport(
        name=cfg.name,
        requests_per_month=round(req_month),
        protea_requests_per_month=round(protea_req),
        tokens_per_month={"input": round(in_tokens), "output": round(out_tokens), "protea_output": round(protea_out)},
        frontier_cost_usd=round(frontier_cost, 2),
        protea_fixed_usd=round(fixed, 2),
        protea_variable_usd=round(variable, 2),
        protea_cost_usd=round(protea_cost, 2),
        savings_usd=round(savings, 2),
        savings_share=round(savings / frontier_cost, 4) if frontier_cost else 0.0,
        gpu_hours_needed=round(gpu_hours, 1),
        gpu_utilisation=round(utilisation, 4),
        capacity_requests_per_month=round(capacity_requests),
        breakeven_requests_per_month=round(breakeven_req) if breakeven_req != float("inf") else -1,
        breakeven_utilisation=round(breakeven_util, 4) if breakeven_util != float("inf") else -1,
        breakeven_multiple_of_forecast=round(multiple, 3) if multiple is not None else None,
        kill_signal=kill,
        verdict=verdict,
        assumptions=[
            f"blended frontier price {p_in:.2f}/{p_out:.2f} USD per M input/output tokens",
            f"{cfg.gpu.count}× {cfg.gpu.name} at {cfg.gpu.usd_per_hour:.2f} USD/h ({cfg.gpu.provider}), "
            f"{cfg.gpu.output_tokens_per_second:g} output tok/s each",
            f"{ov.frontier_fallback_share:.0%} of Protea traffic falls back to frontier models",
            f"GPU capacity sized for {cfg.gpu.target_utilisation:.0%} target utilisation",
            "GPU time is a fixed monthly cost whether or not requests arrive; frontier cost scales with volume",
        ],
    )


def render_markdown(report: EconomicsReport) -> str:
    rows = [
        ("Requests / month (all)", f"{report.requests_per_month:,.0f}"),
        ("Requests / month routable to Protea", f"{report.protea_requests_per_month:,.0f}"),
        ("Frontier-only cost", f"{report.frontier_cost_usd:,.2f} USD"),
        ("Protea fixed cost (GPU, training, engineering, ops)", f"{report.protea_fixed_usd:,.2f} USD"),
        ("Protea variable cost (non-Protea traffic + fallbacks)", f"{report.protea_variable_usd:,.2f} USD"),
        ("Protea total", f"{report.protea_cost_usd:,.2f} USD"),
        ("Savings at forecast", f"{report.savings_usd:,.2f} USD ({report.savings_share:.0%})"),
        ("GPU utilisation at forecast", f"{report.gpu_utilisation:.0%}"),
        ("Capacity at target utilisation", f"{report.capacity_requests_per_month:,.0f} requests / month"),
        ("Break-even volume", f"{report.breakeven_requests_per_month:,.0f} requests / month"),
        ("Break-even utilisation", f"{report.breakeven_utilisation:.0%}"),
        (
            "Break-even ÷ forecast",
            f"{report.breakeven_multiple_of_forecast}" if report.breakeven_multiple_of_forecast is not None else "n/a",
        ),
        ("Kill signal", "yes" if report.kill_signal else "no"),
    ]
    lines = [f"# Economic model — {report.name}", "", "| Metric | Value |", "|---|---|"]
    lines += [f"| {k} | {v} |" for k, v in rows]
    lines += ["", f"**Verdict:** {report.verdict}", "", "Assumptions:", *[f"- {a}" for a in report.assumptions]]
    return "\n".join(lines) + "\n"


def as_dict(report: EconomicsReport) -> dict[str, Any]:
    return report.model_dump(mode="json")
