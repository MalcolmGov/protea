"""Facade configuration (`configs/serve/*.yaml`, kind `serve`)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ServeConfig(BaseModel):
    backend: str = "protea"  # provider name from `protea providers list`
    backend_model: str | None = None  # overrides the provider's configured model
    served_model: str = "protea-agent"  # the name callers use (and the name reported back)
    aliases: list[str] = Field(default_factory=list)  # extra accepted model names, e.g. gpt-4o-mini for drop-in use
    host: str = "0.0.0.0"  # container-facing bind address; override per deployment
    port: int = 8080
    require_token: bool = True  # bearer token from PROTEA_FACADE_TOKEN
    ready_ttl_s: float = 15.0  # how long a backend health probe is trusted
    drain_timeout_s: float = 30.0  # graceful shutdown: wait for in-flight requests up to this long
    max_repairs: int = Field(default=1, ge=0, le=3)  # validation gate repair rounds
    routing_policy: str | None = None  # configs/routing/*.yaml; mounts /v1/route/* when set
    route_events: str | None = None  # JSONL file for route/fallback events (default: application log)
    workers: int = 1

    def model_aliases(self) -> set[str]:
        return {self.served_model, *self.aliases}
