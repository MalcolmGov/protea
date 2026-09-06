"""Inference facade: OpenAI-compatible gateway, native contract, validation gate, health/readiness/metrics."""

from protea.serving.config import ServeConfig
from protea.serving.gate import GateResult, generate_validated, parse_and_validate

__all__ = ["GateResult", "ServeConfig", "generate_validated", "parse_and_validate"]
