# deployment/protea

| File | Purpose |
|---|---|
| `Dockerfile.infer` + `entrypoint-infer.sh` | vLLM OpenAI-compatible engine; non-root; adapter mounted at `/adapters`, weights in `/data/hf`; SIGTERM drains |
| `Dockerfile.facade` | CPU facade (`protea serve facade`): auth, validation gate, `/healthz` `/readyz` `/metrics` |
| `Dockerfile.train` + `entrypoint-train.sh` | image referenced by the remote job adapters (`ghcr.io/malcolmgov/protea-train`); the entrypoint loads object-store credentials, pulls the dataset, streams checkpoints to storage, enforces the runtime limit and pushes the final adapter |
| `docker-compose.yml` | engine + facade on one GPU host |

See `docs/inference.md` for wiring aria's runtime (`MIAI_MODEL_GATEWAY_URL`) to the facade, sizing and the execution boundary.

Dependencies in every image come from hash-pinned locks under `requirements/` (`base.txt`, `serve.txt`, `train-gpu.txt`), installed wheels-only with `--require-hashes`; the Protea package itself is used from source via `PYTHONPATH`. The engine image layers only the base runtime onto the vLLM image, so version ranges pinned by vLLM stay untouched.
