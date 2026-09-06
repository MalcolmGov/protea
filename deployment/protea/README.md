# deployment/protea

| File | Purpose |
|---|---|
| `Dockerfile.infer` + `entrypoint-infer.sh` | vLLM OpenAI-compatible engine; adapter mounted at `/adapters`; SIGTERM drains |
| `Dockerfile.facade` | CPU facade (`protea serve facade`): auth, validation gate, `/healthz` `/readyz` `/metrics` |
| `Dockerfile.train` | image referenced by the remote job adapters (`ghcr.io/malcolmgov/protea-train`) |
| `docker-compose.yml` | engine + facade on one GPU host |

See `docs/inference.md` for wiring aria's runtime (`MIAI_MODEL_GATEWAY_URL`) to the facade, sizing and the execution boundary.
