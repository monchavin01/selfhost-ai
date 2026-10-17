#!/usr/bin/env bash
# Container entrypoint / Vast onstart. Starts backend, optional vLLM hunyuan, LiteLLM; prints READY when warmed up.
set -euo pipefail

: "${LITELLM_MASTER_KEY:?LITELLM_MASTER_KEY is required}"
export HF_HOME="${HF_HOME:-/data/hf}"
LOG_DIR="${LOG_DIR:-/data/logs}"; export LOG_DIR
ENABLE_HUNYUAN="${ENABLE_HUNYUAN:-0}"
APP="${APP_DIR:-/app}"
BACKEND_PY=/opt/backend/bin/python
mkdir -p "$LOG_DIR" "$HF_HOME"

pids=()
cleanup() { trap '' TERM INT; kill "${pids[@]}" 2>/dev/null || true; wait 2>/dev/null || true; }
trap 'cleanup; exit 143' TERM INT
trap cleanup EXIT

# Reap one child's exit: any child dying ends the container.
watch_children() {
  while true; do
    for p in "${pids[@]}"; do
      kill -0 "$p" 2>/dev/null || { echo "child $p died" >&2; kill -TERM $$; return; }
    done
    sleep 2
  done
}

ts() { date +%s.%N; }
T_START=$(ts)
since() { awk -v a="$1" -v b="$(ts)" 'BEGIN{printf "%.1fs", b-a}'; }

wait_http() { # url label timeout_s
  local end=$((SECONDS + $3))
  until curl -fsS -o /dev/null "$1" 2>/dev/null; do
    [ $SECONDS -lt $end ] || { echo "timeout waiting for $2" >&2; exit 1; }
    sleep 2
  done
}

# GPU logger
( while true; do
    echo "$(date -u +%FT%TZ) $(nvidia-smi --query-gpu=utilization.gpu,memory.used,memory.total,temperature.gpu --format=csv,noheader 2>&1)"
    sleep 5
  done ) >>"$LOG_DIR/gpu.log" &
pids+=($!)

t=$(ts); "$BACKEND_PY" "$APP/scripts/fetch_models.py"; echo "phase fetch: $(since "$t")"

"$BACKEND_PY" "$APP/scripts/voices.py" verify || echo "WARNING: voice bank mismatch; backend will refuse bad voices" >&2

# CTranslate2 needs the cuDNN 9 / cuBLAS 12 libs bundled with the torch wheels; scoped to the backend only.
NV=$("$BACKEND_PY" -c 'import site;print(site.getsitepackages()[0])')/nvidia
T_LOAD=$(ts); t=$(ts)
(cd "$APP" && LD_LIBRARY_PATH="$NV/cudnn/lib:$NV/cublas/lib:${LD_LIBRARY_PATH:-}" \
  exec "$BACKEND_PY" -m uvicorn backend.app:app --host 127.0.0.1 --port 8001) &
pids+=($!)
wait_http http://127.0.0.1:8001/ready backend 900
echo "backend ready in $(since "$t")"

if [ "$ENABLE_HUNYUAN" = "1" ]; then
  # 24 GB card (3090: 24576 MiB). Backend after load ~10 GB: F5 ~3 + whisper-th turbo ~2.5 + whisper-zh large-v3
  # ~3.5 + CUDA contexts ~1. vLLM budget 0.45 x 24 = ~10.8 GB: fp8 7B weights ~8 GB + ~2 GB KV cache
  # (~130 KB/token, 8 KV heads) = ~15k tokens, enough for 4 seqs x 4096. Leaves ~3 GB slack. Backend must load first.
  t=$(ts)
  HF_HUB_OFFLINE=1 vllm serve "$("$BACKEND_PY" "$APP/scripts/fetch_models.py" --get hunyuan-mt-7b repo)" \
    --revision "$("$BACKEND_PY" "$APP/scripts/fetch_models.py" --get hunyuan-mt-7b revision)" \
    --served-model-name hunyuan-mt-7b --host 127.0.0.1 --port 8002 \
    --gpu-memory-utilization "${HUNYUAN_GPU_UTIL:-0.45}" --max-model-len 4096 --max-num-seqs 4 &
  pids+=($!)
  wait_http http://127.0.0.1:8002/health hunyuan 900
  echo "hunyuan ready in $(since "$t")"
fi

t=$(ts)
/opt/litellm/bin/litellm --config "$APP/litellm.yaml" --host 0.0.0.0 --port 4000 &
pids+=($!)
wait_http http://127.0.0.1:4000/health/liveliness litellm 120
echo "litellm ready in $(since "$t")"
echo "phase load (backend+vllm+litellm): $(since "$T_LOAD")"

watch_children &

T_WARM=$(ts)
# Warm-up: one real request per model through LiteLLM.
AUTH=(-H "Authorization: Bearer $LITELLM_MASTER_KEY")
WAV=$(mktemp --suffix=.wav)
t=$(ts)
curl -fsS -m 120 "${AUTH[@]}" -H 'Content-Type: application/json' \
  -d '{"model":"f5-tts-th","input":"สวัสดีครับ","voice":"default","response_format":"wav"}' \
  -o "$WAV" http://127.0.0.1:4000/v1/audio/speech
[ "$(head -c 4 "$WAV")" = "RIFF" ] || { echo "warm-up: speech did not return RIFF" >&2; exit 1; }
echo "warm-up f5-tts-th $(since "$t")"
for m in whisper-th whisper-zh; do
  t=$(ts)
  curl -fsS -m 120 "${AUTH[@]}" -F "file=@$WAV" -F "model=$m" -F response_format=json \
    -o /dev/null http://127.0.0.1:4000/v1/audio/transcriptions
  echo "warm-up $m $(since "$t")"
done
rm -f "$WAV"
if [ "$ENABLE_HUNYUAN" = "1" ]; then
  t=$(ts)
  curl -fsS -m 120 "${AUTH[@]}" -H 'Content-Type: application/json' \
    -d '{"model":"hunyuan-mt-7b","messages":[{"role":"user","content":"Translate to English: สวัสดี"}],"max_tokens":16,"temperature":0.7}' \
    -o /dev/null http://127.0.0.1:4000/v1/chat/completions
  echo "warm-up hunyuan-mt-7b $(since "$t")"
fi

echo "phase warm-up: $(since "$T_WARM")"
echo "cold start total: $(since "$T_START")"
echo READY
wait -n "${pids[@]}" || true
echo "a child exited; shutting down" >&2
exit 1
