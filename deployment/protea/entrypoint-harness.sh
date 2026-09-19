#!/usr/bin/env bash
# Agent-harness entrypoint: drive a Protea-served model with DeepSeek Harness (dsh) on a rented GPU. No training,
# no scoring suite: the harness supplies the agent loop, sandbox and tools; Protea supplies the model behind its
# deployed facade (tool-permission guard included), served by vLLM (the production path, ADR-009) or, as a
# fallback, the in-process `local` provider on CUDA. Everything comes from the environment by name (the
# run-harness workflow / remote plan sets these); no secret is baked in and no API token leaves the box.
#
# Per model in PROTEA_HARNESS_MODELS ("<inference-config>@<hub-revision>;..."): start the engine, start the facade
# in front of it, run three headless tasks in a seeded scratch workspace, save each transcript, stop both. The
# transcripts, summaries and this log are streamed to storage under harness-reports/<run_id>/ and logs/.
#
# Deliberately no `set -e`: every phase's status is captured and the log and results are always pushed.
set -uo pipefail

WORKDIR="${PROTEA_WORKDIR:-/workspace/protea}"
# Log to /tmp (always writable by the image's unprivileged user; $WORKDIR is root-owned) and ship it on any exit,
# so a failure here is diagnosable off-box even though the pod self-deletes. Same pattern as entrypoint-eval.sh.
LOGDIR="${PROTEA_LOGDIR:-/tmp/protea/logs}"
mkdir -p "$LOGDIR"
LOG="$LOGDIR/harness-${PROTEA_RUN_ID:-unknown}.log"
exec >"$LOG" 2>&1
echo "protea-harness: start (run=${PROTEA_RUN_ID:-unknown}, engine=${PROTEA_HARNESS_ENGINE:-vllm}, composition=${PROTEA_HARNESS_COMPOSITION:-minimal})"

# Object-store auth up front so the log push works even if a required-variable check below aborts the run.
_CREDS="${PROTEA_STORAGE_CREDENTIALS:-}"
export AWS_ACCESS_KEY_ID="${_CREDS%%:*}"
export AWS_SECRET_ACCESS_KEY="${_CREDS#*:}"
if [ -n "${AWS_ENDPOINT_URL:-}" ]; then export AWS_DEFAULT_REGION="${AWS_DEFAULT_REGION:-auto}"; fi

# All writes under /tmp (always writable); $WORKDIR is read-only for the job user.
WORKBASE="${PROTEA_HARNESS_WORKBASE:-/tmp/protea/harness}"
OUT="$WORKBASE/out/${PROTEA_RUN_ID:-run}"
mkdir -p "$OUT"

# push_state ships the log AND every transcript written so far: a host reclaim mid-run must not lose the
# finished models' results (same reasoning as entrypoint-synth.sh). Directories → protea-storage push (aws s3 sync).
push_state() {
  protea-storage push "$LOGDIR" "${PROTEA_STORAGE:-}" >/dev/null 2>&1 || true
  protea-storage push "$OUT" "${PROTEA_STORAGE:-}/harness-reports" >/dev/null 2>&1 || true
}
SYNC_PID=""
if [ -n "${PROTEA_STORAGE:-}" ] && [ -n "$AWS_ACCESS_KEY_ID" ]; then
  trap push_state EXIT
  ( while sleep 45; do push_state; done ) &
  SYNC_PID=$!
fi

: "${PROTEA_STORAGE:?PROTEA_STORAGE (e.g. s3://bucket) must be set}"
: "${PROTEA_STORAGE_CREDENTIALS:?PROTEA_STORAGE_CREDENTIALS (\"<access_key_id>:<secret_access_key>\") must be set}"
: "${HF_TOKEN:?HF_TOKEN must be set (to download the base model)}"

# Knobs (forwarded by the launcher; see run-harness.yml). Defaults are the two v0 candidates at their pinned commits.
MODELS="${PROTEA_HARNESS_MODELS:-configs/inference/vllm-qwen3-4b.yaml@1cfa9a7208912126459214e8b04321603b3df60c;configs/inference/vllm-qwen3-8b.yaml@b968826d9c46dd6066d109eabc6255188de91218}"
ENGINE="${PROTEA_HARNESS_ENGINE:-vllm}"                 # vllm | local
COMPOSITION="${PROTEA_HARNESS_COMPOSITION:-minimal}"    # minimal (one bash tool) | standard (the harness's full roster)
THINKING="${PROTEA_HARNESS_THINKING:-false}"            # false | true | default (chat template's own default)
SYSTEM_PROMPT_FILE="${PROTEA_HARNESS_SYSTEM_PROMPT_FILE:-}"   # e.g. configs/evaluation/guardrail-system-prompt.md
DSH_VERSION="${PROTEA_HARNESS_DSH_VERSION:-0.1.5-rc.2}"
NODE_VERSION="${PROTEA_HARNESS_NODE_VERSION:-22.22.2}"
VLLM_VERSION="${PROTEA_HARNESS_VLLM_VERSION:-0.11.0}"   # pins torch==2.8.0, the image's own torch (no re-download)
TASK_TIMEOUT="${PROTEA_HARNESS_TASK_TIMEOUT_S:-600}"
# The launcher passes the training config's runtime limit (hours); a harness run that needs more than 75 min is
# stuck, so cap it here — every wait below is bounded and the deadline is checked before each model.
MAX_MINUTES="${PROTEA_MAX_RUNTIME_MINUTES:-75}"
[ "$MAX_MINUTES" -gt 75 ] 2>/dev/null && MAX_MINUTES=75
DEADLINE=$(( $(date +%s) + MAX_MINUTES * 60 ))

cd "$WORKDIR"
export HOME="${HOME:-/home/protea}"
export PATH="$HOME/.local/bin:$PATH"

# Loopback-only tokens, minted per run, never written to the log.
mint() { python -c 'import secrets; print(secrets.token_hex(16))'; }
export PROTEA_FACADE_TOKEN; PROTEA_FACADE_TOKEN="$(mint)"
export PROTEA_INFERENCE_TOKEN; PROTEA_INFERENCE_TOKEN="$(mint)"
export PROTEA_INFERENCE_URL="http://127.0.0.1:8000/v1"

case "$THINKING" in
  false) export PROTEA_INFERENCE_EXTRA_BODY='{"chat_template_kwargs": {"enable_thinking": false}}' PROTEA_LOCAL_ENABLE_THINKING=false ;;
  true)  export PROTEA_INFERENCE_EXTRA_BODY='{"chat_template_kwargs": {"enable_thinking": true}}'  PROTEA_LOCAL_ENABLE_THINKING=true ;;
  *)     unset PROTEA_INFERENCE_EXTRA_BODY PROTEA_LOCAL_ENABLE_THINKING ;;
esac

python - "$OUT/run.json" "$MODELS" "$ENGINE" "$COMPOSITION" "$THINKING" "$SYSTEM_PROMPT_FILE" "$DSH_VERSION" "$VLLM_VERSION" <<'PY'
import json, sys, time
keys = ["out", "models", "engine", "composition", "thinking", "system_prompt_file", "dsh_version", "vllm_version"]
d = dict(zip(keys, sys.argv[1:])); d.pop("out"); d["started_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
json.dump(d, open(sys.argv[1], "w"), indent=2)
PY

# ---- 1. Node + the harness (user-space install, install scripts off) ---------------------------------------
echo "protea-harness: installing node $NODE_VERSION and @deepseek-ai/dsh@$DSH_VERSION"
mkdir -p "$WORKBASE/node" "$WORKBASE/dsh"
if ! curl -fsSL "https://nodejs.org/dist/v${NODE_VERSION}/node-v${NODE_VERSION}-linux-x64.tar.xz" | tar -xJ -C "$WORKBASE/node" --strip-components=1; then
  echo "protea-harness: node download failed"; exit 2
fi
export PATH="$WORKBASE/node/bin:$PATH"
if ! ( cd "$WORKBASE/dsh" && npm init -y >/dev/null 2>&1 && npm install --ignore-scripts --no-audit --no-fund "@deepseek-ai/dsh@${DSH_VERSION}" >/dev/null 2>&1 ); then
  echo "protea-harness: dsh install failed"; exit 2
fi
DSH="$WORKBASE/dsh/node_modules/.bin/dsh"
export DSH_HOME="$WORKBASE/dsh/home" DSH_TELEMETRY_MODE=DISABLED DSH_PERMISSION_MODE=workspace-write
mkdir -p "$DSH_HOME"
if ! "$DSH" --profile protea-min --from-default-profile headless --dump-config >/dev/null 2>"$OUT/dsh-profile-init.err"; then
  echo "protea-harness: dsh profile init failed"; cat "$OUT/dsh-profile-init.err"; exit 2
fi
echo "protea-harness: dsh $("$DSH" --version 2>/dev/null) ready"

# The profile patch: the facade as an OpenAI-compatible provider + default model. The `minimal` composition is
# expressed directly (dsh-agent-presets does not compose headless sessions in 0.1.5-rc.2): every host tool but
# bash off, no plan mode / compaction / repeat reminder, the preset's one-line persona.
PATCH="$DSH_HOME/profiles/protea-min/cordis.patch.yml"
cat > "$PATCH" <<'YML'
- id: llm-pi-ai
  config:
    providers:
      protea:
        displayName: Protea facade
        apiKeyEnv: PROTEA_FACADE_TOKEN
        api: openai-completions
        baseURL: http://127.0.0.1:8080/v1
        defaultContextWindow: 8192
        defaultMaxTokens: 2048
        models:
          - id: protea-agent
            name: Protea agent (facade)
            contextWindow: 8192
            maxTokens: 2048
        retryPolicy:
          mode: normal
          maxRetries: 1
- id: agent-default-model
  config:
    provider: protea
    model: protea-agent
- id: session-title-llm
  disabled: true
YML
if [ "$COMPOSITION" = "minimal" ]; then
  for row in tool-jobs tool-fs tool-fs-search tool-skill tool-subagent tool-subagent-fork tool-subagent-control \
             tool-subagent-list-agents tool-workflow tool-todo tool-goal tool-ralph tool-web plan-mode \
             compaction-basic command-compact repeat-tool-reminder; do
    printf -- '- id: %s\n  disabled: true\n' "$row" >> "$PATCH"
  done
  cat >> "$PATCH" <<'YML'
- id: system-prompt
  config:
    personaPrefix: You are a helpful software engineer assistant.
    personaSuffix: ''
YML
fi
cp "$PATCH" "$OUT/cordis.patch.yml"

# Transcript tooling: the session log is zstd-framed JSONL (one frame per append); split on the frame magic.
cat > "$WORKBASE/decode-session.js" <<'JS'
const fs=require("fs"),zlib=require("zlib"),path=require("path");
const [,, root, out] = process.argv;
let all="";
for (const f of fs.readdirSync(root,{recursive:true}).map(p=>path.join(root,p)).filter(p=>p.endsWith(".jsonl.zstd"))) {
  const buf=fs.readFileSync(f); let off=0;
  while (off < buf.length) {
    let next = buf.indexOf(Buffer.from([0x28,0xb5,0x2f,0xfd]), off+4); if (next<0) next=buf.length;
    try { all += zlib.zstdDecompressSync(buf.subarray(off,next)).toString("utf8"); } catch(e){ console.error("frame fail @",off,e.message); }
    off=next;
  }
}
fs.writeFileSync(out, all);
JS
cat > "$WORKBASE/summarize-session.js" <<'JS'
const fs=require("fs");
const lines=fs.readFileSync(process.argv[2],"utf8").trim().split("\n").filter(Boolean).map(l=>JSON.parse(l));
const h=lines.find(e=>e.type==="request/header");
console.log("tools offered:", h ? h.data.header.tools.map(t=>t.name).join(", ") : "(none)");
const sys=lines.find(e=>e.type==="system/message");
console.log("system prompt chars:", sys?sys.data.message.content.map(c=>c.text).join("\n").length:0);
let prev=null, steps=0;
for (const e of lines) {
  if (e.type==="step/start") prev=e.time;
  if (e.type==="assistant/message") {
    steps++;
    if(!e.usage){const uc=(e.data.stream||[]).find(c=>c.chunk&&c.chunk.type==="usage"); if(uc) e.usage=uc.chunk.usage;}
    const lat=prev?((e.time-prev)/1000).toFixed(1)+"s":"?";
    const parts=(e.data.message.content||[]).map(c=>c.type==="tool-call"?`CALL ${c.name} ${c.arguments}`:`TEXT ${JSON.stringify(c.text??"")}`);
    console.log(`[step ${e.data.step}] ${lat}  in=${e.usage?.inputTokens??"?"} out=${e.usage?.outputTokens??"?"}  ${parts.join(" | ").slice(0,600)}`);
  }
  if (e.type==="tool/result") {
    const t=(e.data.message.content||[]).map(c=>(c.content||[]).map(x=>x.text??"").join("")).join("");
    console.log(`         RESULT${e.data.message.content?.[0]?.isError?"(error)":""}: ${JSON.stringify(t).slice(0,500)}`);
  }
}
console.log("steps:", steps);
JS

# ---- 2. Engine dependencies ------------------------------------------------------------------------------
python -c "import pytest" 2>/dev/null || python -m pip install --user --quiet --no-warn-script-location pytest >/dev/null 2>&1 || true
python -c "import fastapi, uvicorn" 2>/dev/null || python -m pip install --user --quiet --no-warn-script-location "fastapi>=0.115" "uvicorn>=0.30" >/dev/null 2>&1 || true
if [ "$ENGINE" = "vllm" ]; then
  echo "protea-harness: pip install vllm==$VLLM_VERSION (user site; torch stays at the image's 2.8.0)"
  T0=$(date +%s)
  if python -m pip install --user --quiet --no-warn-script-location "vllm==${VLLM_VERSION}" "transformers>=4.56,<5" >"$OUT/pip-vllm.log" 2>&1; then
    echo "protea-harness: vllm installed in $(( $(date +%s) - T0 ))s: $(python -c 'import vllm; print(vllm.__version__)' 2>/dev/null)"
  else
    echo "protea-harness: vllm install FAILED after $(( $(date +%s) - T0 ))s — falling back to the in-process local provider on CUDA"
    tail -20 "$OUT/pip-vllm.log"
    ENGINE=local
  fi
fi
python - "$OUT/run.json" "$ENGINE" <<'PY'
import json, sys
p = sys.argv[1]; d = json.load(open(p)); d["engine_used"] = sys.argv[2]; json.dump(d, open(p, "w"), indent=2)
PY

# ---- 3. Facade config: the deployed one, re-pointed at the engine, loopback only, no rate limit ------------
FACADE_CFG="$WORKBASE/facade.yaml"
python - "$WORKDIR/configs/serve/facade.yaml" "$FACADE_CFG" "$ENGINE" "$SYSTEM_PROMPT_FILE" <<'PY'
import sys, yaml
src, dst, engine, prompt = sys.argv[1:5]
cfg = yaml.safe_load(open(src))
cfg["backend"] = "protea" if engine == "vllm" else "local"
cfg["host"], cfg["port"] = "127.0.0.1", 8080
cfg["rate_limit_rpm"] = cfg["rate_limit_burst"] = None
cfg["routing_policy"] = None
cfg.pop("system_prompt", None)
cfg["system_prompt_file"] = prompt or None   # blank = no product overlay (the tool guard stays as deployed)
yaml.safe_dump(cfg, open(dst, "w"), sort_keys=False)
PY
cp "$FACADE_CFG" "$OUT/facade.yaml"

# ---- 4. Seeded workspace: three functions, one deliberate bug, one failing test ----------------------------
WS="$WORKBASE/workspace"
mkdir -p "$WS"
cat > "$WS/calc.py" <<'PYF'
"""Tiny calculator module used by the harness smoke test."""


def add(a, b):
    return a + b


def subtract(a, b):
    return a + b  # BUG: should subtract


def multiply(a, b):
    return a * b
PYF
cat > "$WS/test_calc.py" <<'PYF'
from calc import add, subtract, multiply


def test_add():
    assert add(2, 3) == 5


def test_subtract():
    assert subtract(5, 3) == 2


def test_multiply():
    assert multiply(4, 3) == 12
PYF
printf '# Harness smoke workspace\n\nA tiny Python module with a deliberate bug. Run the tests with `python -m pytest -q`.\n' > "$WS/README.md"
( cd "$WS" && git init -q . && git add -A && git -c user.email=harness@protea -c user.name=harness commit -qm "seed workspace" )

# ---- helpers ----------------------------------------------------------------------------------------------
wait_http() {  # url max_seconds [pid]: poll until 2xx; give up early when the process died
  local url=$1 max=$2 pid=${3:-} t=0
  while [ "$t" -lt "$max" ]; do
    curl -sf "$url" >/dev/null 2>&1 && return 0
    if [ -n "$pid" ] && ! kill -0 "$pid" 2>/dev/null; then return 1; fi
    sleep 5; t=$((t + 5))
  done
  return 1
}
stop_pid() { [ -n "${1:-}" ] && kill "$1" 2>/dev/null; [ -n "${1:-}" ] && ( sleep 20; kill -9 "$1" 2>/dev/null ) & }

run_task() {  # label task-text: one headless dsh run in a clean workspace; transcript + summary under $OUT
  local label=$1; shift; local task="$*"
  local dir="$OUT/$MODEL_LABEL/$label"; mkdir -p "$dir"
  ( cd "$WS" && git checkout -q -- . && git clean -qfd )
  rm -rf "$DSH_HOME/sessions"
  local start; start=$(date +%s)
  ( cd "$WS" && timeout --signal=TERM --kill-after=30 "$TASK_TIMEOUT" "$DSH" --profile protea-min "$task" >"$dir/stdout.txt" 2>"$dir/stderr.txt" )
  local code=$? elapsed=$(( $(date +%s) - start ))
  echo "label=$label exit=$code elapsed=${elapsed}s" > "$dir/meta.txt"
  node "$WORKBASE/decode-session.js" "$DSH_HOME/sessions" "$dir/session.jsonl" >/dev/null 2>&1 || true
  node "$WORKBASE/summarize-session.js" "$dir/session.jsonl" > "$dir/summary.txt" 2>&1 || true
  ( cd "$WS" && git --no-pager diff > "$dir/workspace.diff" 2>/dev/null; python -m pytest -q 2>&1 | tail -2 > "$dir/tests.txt" )
  echo "protea-harness: [$MODEL_LABEL/$label] exit=$code ${elapsed}s :: $(head -c 240 "$dir/stdout.txt" | tr '\n' ' ')"
}

# ---- 5. Per model: engine → facade → tasks → stop -----------------------------------------------------------
IFS=';' read -r -a ENTRIES <<< "$MODELS"
for entry in "${ENTRIES[@]}"; do
  [ -z "$entry" ] && continue
  CFG="${entry%%@*}"; REV="${entry#*@}"; [ "$REV" = "$entry" ] && REV=""
  MODEL_LABEL="$(basename "$CFG" .yaml)"
  mkdir -p "$OUT/$MODEL_LABEL"
  MODEL_ID="$(python -c 'import sys, yaml; print(yaml.safe_load(open(sys.argv[1]))["model"])' "$CFG" 2>/dev/null)"
  if [ -z "$MODEL_ID" ]; then echo "protea-harness: $CFG is not an inference config; skipping"; continue; fi
  if [ "$(date +%s)" -gt $((DEADLINE - 900)) ]; then echo "protea-harness: under 15 min to the deadline; skipping $MODEL_LABEL"; continue; fi
  echo "protea-harness: === $MODEL_LABEL: $MODEL_ID @ ${REV:-main} via $ENGINE ==="
  ENGINE_PID=""; FACADE_PID=""
  if [ "$ENGINE" = "vllm" ]; then
    CMD="$(protea serve vllm --config "$CFG")"
    [ -n "$REV" ] && CMD="$CMD --revision $REV"
    [ "$THINKING" != "false" ] && CMD="$CMD --reasoning-parser qwen3"   # keep any <think> out of the content
    echo "protea-harness: engine: $CMD"   # the token stays a $NAME here; eval expands it
    eval "$CMD" >"$OUT/$MODEL_LABEL/vllm.log" 2>&1 &
    ENGINE_PID=$!
    T0=$(date +%s)
    if ! wait_http "http://127.0.0.1:8000/health" 1200 "$ENGINE_PID"; then
      echo "protea-harness: vllm did not become healthy in $(( $(date +%s) - T0 ))s; tail of its log:"; tail -40 "$OUT/$MODEL_LABEL/vllm.log"
      stop_pid "$ENGINE_PID"; continue
    fi
    echo "protea-harness: vllm healthy after $(( $(date +%s) - T0 ))s"
  else
    export PROTEA_LOCAL_MODEL="$MODEL_ID" PROTEA_LOCAL_REVISION="$REV" PROTEA_LOCAL_SERVED_AS="$MODEL_LABEL"
  fi
  protea serve facade --config "$FACADE_CFG" >"$OUT/$MODEL_LABEL/facade.log" 2>&1 &
  FACADE_PID=$!
  T0=$(date +%s)
  if ! wait_http "http://127.0.0.1:8080/readyz" 1200 "$FACADE_PID"; then
    echo "protea-harness: facade not ready in $(( $(date +%s) - T0 ))s; tail of its log:"; tail -40 "$OUT/$MODEL_LABEL/facade.log"
    stop_pid "$FACADE_PID"; stop_pid "$ENGINE_PID"; continue
  fi
  echo "protea-harness: facade ready after $(( $(date +%s) - T0 ))s: $(curl -s http://127.0.0.1:8080/readyz)"
  curl -s -m 120 -H "Authorization: Bearer $PROTEA_FACADE_TOKEN" -H 'content-type: application/json' \
    http://127.0.0.1:8080/v1/chat/completions \
    -d '{"model":"protea-agent","messages":[{"role":"user","content":"Reply with the single word: ready"}],"max_tokens":16}' \
    > "$OUT/$MODEL_LABEL/smoke.json" 2>&1
  echo "protea-harness: smoke: $(head -c 300 "$OUT/$MODEL_LABEL/smoke.json")"

  run_task hello "Reply with exactly: harness online"
  run_task list "List the files in this directory and say which test fails."
  run_task fix "The test suite in this directory has one failing test. Run python -m pytest -q, find the bug in calc.py, fix it, and run the tests again to confirm they pass."

  stop_pid "$FACADE_PID"; stop_pid "$ENGINE_PID"
  wait "$FACADE_PID" 2>/dev/null; wait "$ENGINE_PID" 2>/dev/null
  sleep 5
  push_state
done

# ---- 6. Summary table ---------------------------------------------------------------------------------------
python - "$OUT" <<'PY'
import json, pathlib, re, sys
out = pathlib.Path(sys.argv[1]); run = json.load(open(out / "run.json"))
rows = ["| Model | Task | Exit | Wall | Steps | Tests after | Final message |", "|---|---|---|---|---|---|---|"]
for meta in sorted(out.glob("*/*/meta.txt")):
    d = meta.parent; m = dict(kv.split("=", 1) for kv in meta.read_text().split())
    summ = (d / "summary.txt").read_text() if (d / "summary.txt").exists() else ""
    steps = re.search(r"^steps: (\d+)", summ, re.M); tests = (d / "tests.txt").read_text().strip().splitlines()
    final = (d / "stdout.txt").read_text().strip().replace("\n", " ").replace("|", "/")[:160] if (d / "stdout.txt").exists() else ""
    rows.append(f"| {d.parent.name} | {d.name} | {m.get('exit')} | {m.get('elapsed')} | {steps.group(1) if steps else '?'} | {tests[-1] if tests else '?'} | {final} |")
hdr = f"# Harness run {out.name}\n\nengine={run.get('engine_used', run['engine'])} composition={run['composition']} thinking={run['thinking']} dsh={run['dsh_version']} prompt={run['system_prompt_file'] or '(none)'}\n\n"
(out / "summary.md").write_text(hdr + "\n".join(rows) + "\n")
print(hdr + "\n".join(rows))
PY

[ -n "$SYNC_PID" ] && kill "$SYNC_PID" 2>/dev/null || true
push_state
echo "protea-harness: done; results under $PROTEA_STORAGE/harness-reports/$(basename "$OUT")"
exit 0
