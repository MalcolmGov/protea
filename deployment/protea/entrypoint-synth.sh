#!/usr/bin/env bash
# Synthesis entrypoint: complete tool-calling seeds with an OPEN-WEIGHT teacher loaded in-process (the `local`
# provider), keeping only completions that satisfy each seed's `expect`. No proprietary teacher, no API tokens
# leave the box — GPU time only, so the teacher runs on the same rented pod as everything else. It pulls the seeds
# from storage, runs the targeted selection + synthesis, and streams the accepted completions plus this log back to
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

# Scratch and the output file — set up BEFORE the background sync loop forks, so the loop can stream partial
# output. Writes go under /tmp (always writable); $WORKDIR is root-owned and the image runs unprivileged.
WORKBASE="${PROTEA_SYNTH_WORKBASE:-/tmp/protea/synth}"
mkdir -p "$WORKBASE"
OUT="$WORKBASE/tool_calling-${PROTEA_RUN_ID:-run}.jsonl"
DEST="${PROTEA_STORAGE%/}/synthetic/$(basename "$OUT")"   # matches synthesize.yml's synthetic/ naming

# push_state ships the log AND whatever accepted completions exist so far. The synthesizer writes completions
# incrementally and flushes, so a periodic copy captures partial work — a SIGKILL (OOM or community-GPU host
# reclaim) mid-run bypasses the EXIT trap, and without this every completed seed would be lost (exactly what
# happened the first run: the pod died in generation and pushed nothing). A single file → aws s3 cp, NOT
# `protea-storage push` (that wraps `aws s3 sync`, which is directory-only and silently no-ops on a file).
push_state() {
  protea-storage push "$LOGDIR" "${PROTEA_STORAGE:-}" >/dev/null 2>&1 || true
  [ -s "$OUT" ] && aws s3 cp "$OUT" "$DEST" --only-show-errors >/dev/null 2>&1 || true
}
SYNC_PID=""
if [ -n "${PROTEA_STORAGE:-}" ] && [ -n "$AWS_ACCESS_KEY_ID" ]; then
  trap push_state EXIT
  ( while sleep 45; do push_state; done ) &
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

cd "$WORKDIR"  # read configs / golden lock from the repo (world-readable); all writes are under $WORKBASE

SEEDS="$WORKBASE/seeds.jsonl"
echo "protea-synth: pulling seeds ${PROTEA_STORAGE%/}/${PROTEA_SEEDS_KEY}"
aws s3 cp "${PROTEA_STORAGE%/}/${PROTEA_SEEDS_KEY}" "$SEEDS" --only-show-errors

# Targeting: forward the selection knobs added to `dataset synthesize`. Scenarios and target tools are
# space-separated in the env; each becomes a repeated flag. Balance spreads the batch across target tools.
SYNTH_ARGS=()
for sc in ${PROTEA_SYNTH_SCENARIOS:-}; do SYNTH_ARGS+=(--scenario "$sc"); done
for tt in ${PROTEA_SYNTH_TARGET_TOOLS:-}; do SYNTH_ARGS+=(--target-tool "$tt"); done
[ "${PROTEA_SYNTH_BALANCE:-1}" = "1" ] && SYNTH_ARGS+=(--balance)

echo "protea-synth: teacher=$TEACHER limit=$LIMIT args=[${SYNTH_ARGS[*]}]"
# `local` is an open-weight, in-process teacher (no API tokens); --confirm satisfies the paid-provider guard.
# The synthesizer writes accepted completions to $OUT incrementally and logs progress every 20 seeds.
timeout --signal=TERM --kill-after=120 "$((MAX_MINUTES * 60))" \
  protea dataset synthesize "$SEEDS" \
    --provider local --model "$TEACHER" --limit "$LIMIT" \
    --golden-lock "$GOLDEN_LOCK" --out "$OUT" --confirm "${SYNTH_ARGS[@]}"
STATUS=$?

[ -n "$SYNC_PID" ] && kill "$SYNC_PID" 2>/dev/null || true
KEPT=0
[ -f "$OUT" ] && KEPT=$(wc -l < "$OUT")
echo "protea-synth: synthesizer exited with status $STATUS; kept $KEPT completions"
# Final push of the accepted completions (they still go through the review lane before they train). Even on a
# non-zero STATUS we ship what exists, so a partial batch is never lost.
if [ -s "$OUT" ]; then
  aws s3 cp "$OUT" "$DEST" --only-show-errors || true
  echo "protea-synth: pushed $KEPT completions to $DEST"
fi

# Best-effort self-stop so a finished pod does not idle-bill (only if the pod was given RUNPOD_API_KEY).
if [ -n "${RUNPOD_API_KEY:-}" ] && [ -n "${RUNPOD_POD_ID:-}" ]; then
  echo "protea-synth: terminating pod $RUNPOD_POD_ID"
  curl -s "https://api.runpod.io/graphql?api_key=${RUNPOD_API_KEY}" \
    -H 'Content-Type: application/json' \
    -d "{\"query\":\"mutation { podTerminate(input: { podId: \\\"${RUNPOD_POD_ID}\\\" }) }\"}" >/dev/null || true
fi

exit "$STATUS"
