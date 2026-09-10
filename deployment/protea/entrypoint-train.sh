#!/usr/bin/env bash
# RunPod (and any container) training entrypoint. The pod disk is disposable, so this wrapper is what makes a
# remote run persist: it loads object-store credentials, pulls the dataset, streams checkpoints to storage while
# training runs, checkpoints on SIGTERM, enforces the runtime limit, and pushes the final adapter before exiting.
# Everything it needs comes from the environment by name (the remote adapter plan sets these); no secret is baked in.
set -euo pipefail

WORKDIR="${PROTEA_WORKDIR:-/workspace/protea}"

# Capture the whole run to a file and ship it to storage on ANY exit — crash, clean no-op, or success. RunPod
# deletes a pod the instant its container exits, taking the console logs with it, so a startup failure is otherwise
# invisible (the console only ever shows "Pod data is no longer available"). Output goes to this file rather than
# the console so it survives off-box; PYTHONUNBUFFERED (set in the image) keeps the trainer's lines flushing to it.
# The push runs from an EXIT trap set as early as the credentials allow, so even a failed required-variable check
# below is still shipped. Read it back with the "Fetch training logs (R2)" workflow.
#
# The log dir must be writable by the image's unprivileged `protea` user; /workspace/protea itself is root-owned, so
# a new dir there would fail (`mkdir: Permission denied`) and kill the run before the trap is even set. Use /tmp,
# which is always writable. The basename stays "logs" so the push lands at $PROTEA_STORAGE/logs regardless.
LOGDIR="${PROTEA_LOGDIR:-/tmp/protea/logs}"
mkdir -p "$LOGDIR"
LOG="$LOGDIR/entrypoint-${PROTEA_RUN_ID:-unknown}.log"
exec >"$LOG" 2>&1
echo "protea-train: entrypoint start (run=${PROTEA_RUN_ID:-unknown}, config=${PROTEA_CONFIG:-unset})"

# Object-store auth, set up front so the log-push trap below works even if a required-variable check aborts the run.
# The credential is "<access_key_id>:<secret_access_key>"; the key id never contains a colon, so split on the first
# one. AWS_ENDPOINT_URL is already in the environment for non-AWS stores (Cloudflare R2, MinIO); such stores use
# region "auto". aws-cli and boto3 read all four of these from the environment.
_CREDS="${PROTEA_STORAGE_CREDENTIALS:-}"
export AWS_ACCESS_KEY_ID="${_CREDS%%:*}"
export AWS_SECRET_ACCESS_KEY="${_CREDS#*:}"
if [ -n "${AWS_ENDPOINT_URL:-}" ]; then export AWS_DEFAULT_REGION="${AWS_DEFAULT_REGION:-auto}"; fi

push_log() { protea-storage push "$LOGDIR" "${PROTEA_STORAGE:-}" >/dev/null 2>&1 || true; }
if [ -n "${PROTEA_STORAGE:-}" ] && [ -n "$AWS_ACCESS_KEY_ID" ]; then trap push_log EXIT; fi

: "${HF_TOKEN:?HF_TOKEN must be set}"
: "${PROTEA_CONFIG:?PROTEA_CONFIG must be set}"
: "${PROTEA_RUN_ID:?PROTEA_RUN_ID must be set}"
: "${PROTEA_STORAGE:?PROTEA_STORAGE (the checkpoint store, e.g. s3://bucket/prefix) must be set}"
: "${PROTEA_STORAGE_CREDENTIALS:?PROTEA_STORAGE_CREDENTIALS (\"<access_key_id>:<secret_access_key>\") must be set}"
MAX_MINUTES="${PROTEA_MAX_RUNTIME_MINUTES:-300}"
SYNC_MINUTES="${PROTEA_SYNC_MINUTES:-10}"

cd "$WORKDIR"

# Dataset and any prior checkpoints live on storage that outlives the instance, not in the image.
protea-storage pull "$PROTEA_STORAGE" "$WORKDIR/protea_data" "$WORKDIR/checkpoints" || true

sync_loop() { while sleep "$((SYNC_MINUTES * 60))"; do protea-storage push "$WORKDIR/checkpoints" "$PROTEA_STORAGE" || true; done; }
sync_loop & SYNC_PID=$!

checkpoint_and_exit() {
  echo "protea-train: SIGTERM, syncing checkpoints before exit"
  kill "$SYNC_PID" 2>/dev/null || true
  protea-storage push "$WORKDIR/checkpoints" "$PROTEA_STORAGE" || true
  exit 143
}
trap checkpoint_and_exit TERM INT

# `--eval` scores the adapter against a judge-free suite right after training and writes the report into the run
# dir (so the push below carries it). Override the suite with PROTEA_EVAL_CONFIG; the eval never fails the run.
EVAL_ARGS=(--eval)
[ -n "${PROTEA_EVAL_CONFIG:-}" ] && EVAL_ARGS+=(--eval-config "$PROTEA_EVAL_CONFIG")
set +e
timeout --signal=TERM --kill-after=120 "$((MAX_MINUTES * 60))" \
  protea train local --config "$PROTEA_CONFIG" --run-id "$PROTEA_RUN_ID" --allow-unregistered "${EVAL_ARGS[@]}"
STATUS=$?
set -e

kill "$SYNC_PID" 2>/dev/null || true
echo "protea-train: trainer exited with status $STATUS"
# The final push must succeed — this is the run's only durable output — so it is not guarded with `|| true`.
protea-storage push "$WORKDIR/checkpoints" "$PROTEA_STORAGE"
echo "protea-train: adapter and checkpoints synced to $PROTEA_STORAGE (exit $STATUS)"

# Best-effort self-stop so a finished pod does not idle-bill. Only fires if the pod was given RUNPOD_API_KEY
# (opt-in: the launch does not inject it by default). Without it, stop the pod yourself once the sync above is done.
if [ -n "${RUNPOD_API_KEY:-}" ] && [ -n "${RUNPOD_POD_ID:-}" ]; then
  echo "protea-train: terminating pod $RUNPOD_POD_ID"
  curl -s "https://api.runpod.io/graphql?api_key=${RUNPOD_API_KEY}" \
    -H 'Content-Type: application/json' \
    -d "{\"query\":\"mutation { podTerminate(input: { podId: \\\"${RUNPOD_POD_ID}\\\" }) }\"}" >/dev/null || true
fi

exit "$STATUS"
