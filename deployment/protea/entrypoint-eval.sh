#!/usr/bin/env bash
# Eval-only entrypoint: score an ALREADY-TRAINED adapter against ZaraBench, judge-free, in-process (the `local`
# provider loads base + LoRA and merges — no tokens leave the box, only GPU time). No training. It pulls the
# adapter from storage, runs the suite, and pushes the report plus this log back to storage. Everything comes from
# the environment by name (the run-eval workflow / remote plan sets these); no secret is baked in.
#
# Deliberately no `set -e`: we capture the evaluator's exit status and always run the log push and the report push.
set -uo pipefail

WORKDIR="${PROTEA_WORKDIR:-/workspace/protea}"
# Log to /tmp (always writable by the image's unprivileged user; $WORKDIR is root-owned) and ship it on any exit,
# so a failure here is diagnosable off-box even though the pod self-deletes. Same pattern as entrypoint-train.sh.
LOGDIR="${PROTEA_LOGDIR:-/tmp/protea/logs}"
mkdir -p "$LOGDIR"
LOG="$LOGDIR/eval-${PROTEA_RUN_ID:-unknown}.log"
exec >"$LOG" 2>&1
echo "protea-eval: start (run=${PROTEA_RUN_ID:-unknown}, adapter=${PROTEA_ADAPTER_KEY:-unset})"

# Object-store auth up front so the log push works even if a required-variable check below aborts the run.
_CREDS="${PROTEA_STORAGE_CREDENTIALS:-}"
export AWS_ACCESS_KEY_ID="${_CREDS%%:*}"
export AWS_SECRET_ACCESS_KEY="${_CREDS#*:}"
if [ -n "${AWS_ENDPOINT_URL:-}" ]; then export AWS_DEFAULT_REGION="${AWS_DEFAULT_REGION:-auto}"; fi

push_log() { protea-storage push "$LOGDIR" "${PROTEA_STORAGE:-}" >/dev/null 2>&1 || true; }
SYNC_PID=""
if [ -n "${PROTEA_STORAGE:-}" ] && [ -n "$AWS_ACCESS_KEY_ID" ]; then
  trap push_log EXIT
  # Also stream the log periodically: an OOM or host reclaim SIGKILLs the container and bypasses the EXIT trap, so
  # without this a death during model load leaves no trace (exactly what happened the first time). 45s cadence.
  ( while sleep 45; do protea-storage push "$LOGDIR" "$PROTEA_STORAGE" >/dev/null 2>&1 || true; done ) &
  SYNC_PID=$!
fi

: "${PROTEA_STORAGE:?PROTEA_STORAGE (e.g. s3://bucket) must be set}"
: "${PROTEA_STORAGE_CREDENTIALS:?PROTEA_STORAGE_CREDENTIALS (\"<access_key_id>:<secret_access_key>\") must be set}"
: "${HF_TOKEN:?HF_TOKEN must be set (to download the base model)}"
# PROTEA_ADAPTER_KEY is OPTIONAL: set -> score base + that adapter; unset -> score the bare base (the B0 baseline).
BASE_MODEL="${PROTEA_BASE_MODEL:-Qwen/Qwen3-8B}"
BASE_REVISION="${PROTEA_BASE_REVISION:-}"   # pin the base to an exact Hub commit so the baseline is reproducible
# ZaraBench 0.2 is the default: it carries the content floors and affirmative checks (docs/zarabench.md).
# 0.1.1 remains available for the P0-P0.2 lineage by setting PROTEA_EVAL_CONFIG explicitly, but its numbers are
# only quotable as published — the evaluators those runs used are not today's.
EVAL_CONFIG="${PROTEA_EVAL_CONFIG:-configs/evaluation/zarabench-0.2.yaml}"
PER_CATEGORY="${PROTEA_EVAL_PER_CATEGORY:-2}"
# "full"/"all"/"0" runs the WHOLE suite. A blank cannot reach here to mean that: the launcher drops empty
# PROTEA_* vars, and the :-2 default above would re-fill 2 — so a non-empty sentinel is the only way to ask for
# the full suite through the workflow (this is what made both B0 runs a 20-task sample).
case "$PER_CATEGORY" in full|all|0|"") PER_CATEGORY="" ;; esac
# The launcher sets PROTEA_MAX_RUNTIME_MINUTES from the *training* config's budget (300 min), which has nothing to
# do with how long an eval needs — inherited blindly, a wedged run burns the whole training budget. The eval's own
# cap (PROTEA_EVAL_MAX_MINUTES) takes precedence when set.
MAX_MINUTES="${PROTEA_EVAL_MAX_MINUTES:-${PROTEA_MAX_RUNTIME_MINUTES:-60}}"

# Read configs/tasks from the repo (world-readable), but WRITE only under a writable base: $WORKDIR
# (/workspace/protea) is root-owned and the image runs as the unprivileged `protea` user, so writing the adapter
# and report dirs there fails EACCES — the adapter never downloads, every task errors, and no report is pushed.
# Same class of bug as the training entrypoint's log dir; keep all scratch under /tmp (always writable here).
cd "$WORKDIR"
WORKBASE="${PROTEA_EVAL_WORKBASE:-/tmp/protea/eval}"

# Fail fast when the host has no working GPU. Only the GPU is being rented, and a torch that cannot see CUDA runs
# the suite on the CPU instead: on 2026-09-16 a community host did exactly that ("CUDA unknown error ... setting
# the available devices to zero"), the first task took 56 minutes instead of seconds, and the ETA read 193 hours
# against a billing cap of hours. Checked before the model load, so a bad host costs a minute rather than the run.
if ! python -c '
import sys
import torch
ok = torch.cuda.is_available()
print("device:", torch.cuda.get_device_name(0) if ok else "no CUDA device visible")
sys.exit(0 if ok else 1)
'; then
  echo "protea-eval: refusing to score on CPU — this host cannot see CUDA (device line above)"
  exit 3
fi

export PROTEA_LOCAL_MODEL="$BASE_MODEL"
export PROTEA_LOCAL_REVISION="$BASE_REVISION"
# The adapter is optional. With a key: pull it from storage (a dir of adapter_config.json + weights) and load
# base+LoRA via PEFT. Without one: score the bare pinned base — the B0 baseline the whole delta report compares to.
if [ -n "${PROTEA_ADAPTER_KEY:-}" ]; then
  ADAPTER_DIR="$WORKBASE/adapter"
  mkdir -p "$ADAPTER_DIR"
  echo "protea-eval: pulling adapter ${PROTEA_STORAGE%/}/${PROTEA_ADAPTER_KEY}"
  aws s3 cp "${PROTEA_STORAGE%/}/${PROTEA_ADAPTER_KEY}" "$ADAPTER_DIR" --recursive --only-show-errors
  export PROTEA_LOCAL_ADAPTER="$ADAPTER_DIR"
  export PROTEA_LOCAL_SERVED_AS="${PROTEA_SERVED_AS:-$PROTEA_ADAPTER_KEY}"
  SCORING_LABEL="$BASE_MODEL + $PROTEA_ADAPTER_KEY"
else
  export PROTEA_LOCAL_SERVED_AS="${PROTEA_SERVED_AS:-${BASE_MODEL}@${BASE_REVISION:-main}}"
  SCORING_LABEL="$BASE_MODEL @ ${BASE_REVISION:-main} (bare base — B0)"
fi

OUT="$WORKBASE/out/${PROTEA_RUN_ID:-run}"
mkdir -p "$OUT"

# Optional flags: an empty PER_CATEGORY (from the "full"/"all"/"0" sentinel above) means the full suite — omit
# the flag entirely (typer rejects an empty --per-category value).
EVAL_ARGS=()
[ -n "$PER_CATEGORY" ] && EVAL_ARGS+=(--per-category "$PER_CATEGORY")
[ -n "${PROTEA_EVAL_CATEGORIES:-}" ] && EVAL_ARGS+=(--categories "$PROTEA_EVAL_CATEGORIES")
# Optional product/system-prompt overlay (a repo path baked into the image), to score the base under its
# production guardrail framing — the Exp 0 "is the guardrail gain a prompt effect?" run.
[ -n "${PROTEA_EVAL_SYSTEM_PROMPT_FILE:-}" ] && EVAL_ARGS+=(--system-prompt-file "$PROTEA_EVAL_SYSTEM_PROMPT_FILE")

# Qwen3 reasoning mode (PROTEA_LOCAL_ENABLE_THINKING) is read straight from the env by settings — the local
# provider forwards it to the chat template. Not a CLI flag; just surface it in the log for observability. Unset
# leaves the template default (the sealed instrument); "false" is the base-scoring path (thinking-on fills
# max_tokens with a <think> trace every round, ~100x slower/task, and times the pod out before scoring finishes).
echo "protea-eval: scoring $SCORING_LABEL on $EVAL_CONFIG (per_category=${PER_CATEGORY:-full}, thinking=${PROTEA_LOCAL_ENABLE_THINKING:-template-default})"
# `local` is a free provider and the suite's judge is null, so this spends no API tokens; --confirm is harmless.
timeout --signal=TERM --kill-after=120 "$((MAX_MINUTES * 60))" \
  protea evaluate run --provider local --model "$BASE_MODEL" \
    --config "$EVAL_CONFIG" --out "$OUT" --confirm "${EVAL_ARGS[@]}"
STATUS=$?

[ -n "$SYNC_PID" ] && kill "$SYNC_PID" 2>/dev/null || true
echo "protea-eval: evaluator exited with status $STATUS"
# Ship the reports (small JSON + Markdown) to storage under eval-reports/<run_id>/.
protea-storage push "$OUT" "$PROTEA_STORAGE/eval-reports" || true
echo "protea-eval: reports pushed to $PROTEA_STORAGE/eval-reports/$(basename "$OUT") (exit $STATUS)"

# Best-effort self-stop so a finished pod does not idle-bill (only if the pod was given RUNPOD_API_KEY).
if [ -n "${RUNPOD_API_KEY:-}" ] && [ -n "${RUNPOD_POD_ID:-}" ]; then
  echo "protea-eval: terminating pod $RUNPOD_POD_ID"
  curl -s "https://api.runpod.io/graphql?api_key=${RUNPOD_API_KEY}" \
    -H 'Content-Type: application/json' \
    -d "{\"query\":\"mutation { podTerminate(input: { podId: \\\"${RUNPOD_POD_ID}\\\" }) }\"}" >/dev/null || true
fi

exit "$STATUS"
