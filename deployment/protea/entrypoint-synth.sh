#!/usr/bin/env bash
# Synthesis entrypoint: complete tool-calling seeds with an OPEN-WEIGHT teacher loaded in-process (the `local`
# provider), keeping only completions that satisfy each seed's `expect`. No proprietary teacher, no API tokens
# leave the box — GPU time only, so the teacher runs on the same rented pod as everything else. It pulls the seeds
# from storage, runs the targeted selection + synthesis, and pushes the accepted completions plus this log back to
# storage. Everything comes from the environment by name (the run-synth workflow / remote plan sets these).
#
# Deliberately no `set -e`: we capture the synthesizer's exit status and always run the log push and the output push.
set -uo pipefail

WORKDIR="${PROTEA_WORKDIR:-/workspace/protea}"
# Log to /tmp (always writable by the image's unprivileged user; $WORKDIR is root-owned) and ship it on any exit,
# so a failure here is diagnosable off-box even though the pod self-deletes. Same pattern as entrypoint-eval.sh.
LOGDIR="${PROTEA_LOGDIR:-/tmp/protea/logs}"
mkdir -p "$LOGDIR"
LOG="$LOGDIR/synth-${PROTEA_RUN_ID:-unknown}.log"
exec >"$LOG" 2>&1
echo "protea-synth: start (run=${PROTEA_RUN_ID:-unknown}, seeds=${PROTEA_SEEDS_KEY:-unset})"

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
  # a death during teacher load still leaves a trace. 45s cadence, same as the eval entrypoint.
  ( while sleep 45; do protea-storage push "$LOGDIR" "$PROTEA_STORAGE" >/dev/null 2>&1 || true; done ) &
  SYNC_PID=$!
fi

: "${PROTEA_STORAGE:?PROTEA_STORAGE (e.g. s3://bucket) must be set}"
: "${PROTEA_STORAGE_CREDENTIALS:?PROTEA_STORAGE_CREDENTIALS (\"<access_key_id>:<secret_access_key>\") must be set}"
: "${PROTEA_SEEDS_KEY:?PROTEA_SEEDS_KEY (storage key of the tool_calling_seeds.jsonl to synthesize from) must be set}"
: "${HF_TOKEN:?HF_TOKEN must be set (to download the teacher model)}"
TEACHER="${PROTEA_SYNTH_MODEL:-Qwen/Qwen3-8B}"
LIMIT="${PROTEA_SYNTH_LIMIT:-340}"
GOLDEN_LOCK="${PROTEA_SYNTH_GOLDEN_LOCK:-evaluation/zarabench/0.1/golden.lock}"
MAX_MINUTES="${PROTEA_MAX_RUNTIME_MINUTES:-120}"

# Read the golden lock / repo files from $WORKDIR (world-readable), but WRITE only under /tmp — $WORKDIR is
# root-owned and the image runs unprivileged, so writing scratch there fails EACCES (the lesson from the eval fix).
cd "$WORKDIR"
WORKBASE="${PROTEA_SYNTH_WORKBASE:-/tmp/protea/synth}"
mkdir -p "$WORKBASE"

SEEDS="$WORKBASE/seeds.jsonl"
echo "protea-synth: pulling seeds ${PROTEA_STORAGE%/}/${PROTEA_SEEDS_KEY}"
aws s3 cp "${PROTEA_STORAGE%/}/${PROTEA_SEEDS_KEY}" "$SEEDS" --only-show-errors

# Targeting: forward the selection knobs added to `dataset synthesize`. Scenarios and target tools are
# space-separated in the env; each becomes a repeated flag. Balance spreads the batch across target tools.
SYNTH_ARGS=()
for sc in ${PROTEA_SYNTH_SCENARIOS:-}; do SYNTH_ARGS+=(--scenario "$sc"); done
for tt in ${PROTEA_SYNTH_TARGET_TOOLS:-}; do SYNTH_ARGS+=(--target-tool "$tt"); done
[ "${PROTEA_SYNTH_BALANCE:-1}" = "1" ] && SYNTH_ARGS+=(--balance)

OUT="$WORKBASE/synthetic_tool_calling.jsonl"
echo "protea-synth: teacher=$TEACHER limit=$LIMIT args=[${SYNTH_ARGS[*]}]"
# `local` is an open-weight, in-process teacher (no API tokens); --confirm satisfies the paid-provider guard.
timeout --signal=TERM --kill-after=120 "$((MAX_MINUTES * 60))" \
  protea dataset synthesize "$SEEDS" \
    --provider local --model "$TEACHER" --limit "$LIMIT" \
    --golden-lock "$GOLDEN_LOCK" --out "$OUT" --confirm "${SYNTH_ARGS[@]}"
STATUS=$?

[ -n "$SYNC_PID" ] && kill "$SYNC_PID" 2>/dev/null || true
KEPT=0
[ -f "$OUT" ] && KEPT=$(wc -l < "$OUT")
echo "protea-synth: synthesizer exited with status $STATUS; kept $KEPT completions"
# Ship the accepted completions (they still go through the review lane before they train).
if [ -f "$OUT" ] && [ "$KEPT" -gt 0 ]; then
  DEST="$PROTEA_STORAGE/synthetic"
  protea-storage push "$OUT" "$DEST" || true
  echo "protea-synth: pushed $KEPT completions to $DEST/$(basename "$OUT")"
fi

# Best-effort self-stop so a finished pod does not idle-bill (only if the pod was given RUNPOD_API_KEY).
if [ -n "${RUNPOD_API_KEY:-}" ] && [ -n "${RUNPOD_POD_ID:-}" ]; then
  echo "protea-synth: terminating pod $RUNPOD_POD_ID"
  curl -s "https://api.runpod.io/graphql?api_key=${RUNPOD_API_KEY}" \
    -H 'Content-Type: application/json' \
    -d "{\"query\":\"mutation { podTerminate(input: { podId: \\\"${RUNPOD_POD_ID}\\\" }) }\"}" >/dev/null || true
fi

exit "$STATUS"
