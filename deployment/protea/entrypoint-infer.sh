#!/usr/bin/env bash
# Renders the vLLM command from the inference config and runs it as PID 1's child with signal forwarding,
# so `docker stop` / Kubernetes preStop drains in-flight requests instead of killing them.
set -euo pipefail
CONFIG="${PROTEA_INFERENCE_CONFIG:-/app/configs/inference/vllm-qwen3-8b.yaml}"
ADAPTER_ARG=()
if [ -n "${PROTEA_ADAPTER_PATH:-}" ]; then ADAPTER_ARG=(--adapter "$PROTEA_ADAPTER_PATH"); fi

CMD="$(python -m protea.cli serve vllm --config "$CONFIG" "${ADAPTER_ARG[@]}")"
echo "protea-infer: $CMD"
# shellcheck disable=SC2086
eval "$CMD" &
CHILD=$!
graceful() { echo "protea-infer: SIGTERM, draining"; kill -TERM "$CHILD" 2>/dev/null || true; wait "$CHILD"; exit 0; }
trap graceful TERM INT
wait "$CHILD"
