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
EVAL_CONFIG="${PROTEA_EVAL_CONFIG:-configs/evaluation/zarabench-0.1.yaml}"
PER_CATEGORY="${PROTEA_EVAL_PER_CATEGORY:-2}"
MAX_MINUTES="${PROTEA_MAX_RUNTIME_MINUTES:-60}"

# Read configs/tasks from the repo (world-readable), but WRITE only under a writable base: $WORKDIR
# (/workspace/protea) is root-owned and the image runs as the unprivileged `protea` user, so writing the adapter
# and report dirs there fails EACCES — the adapter never downloads, every task errors, and no report is pushed.
# Same class of bug as the training entrypoint's log dir; keep all scratch under /tmp (always writable here).
cd "$WORKDIR"
WORKBASE="${PROTEA_EVAL_WORKBASE:-/tmp/protea/eval}"

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

# Optional flags: a blank per_category means the full suite (don't pass an empty --per-category, typer rejects it).
EVAL_ARGS=()
[ -n "$PER_CATEGORY" ] && EVAL_ARGS+=(--per-category "$PER_CATEGORY")
[ -n "${PROTEA_EVAL_CATEGORIES:-}" ] && EVAL_ARGS+=(--categories "$PROTEA_EVAL_CATEGORIES")

echo "protea-eval: scoring $SCORING_LABEL on $EVAL_CONFIG (per_category=${PER_CATEGORY:-full})"
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
