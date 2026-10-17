#!/usr/bin/env bash
# Vast On-start entrypoint: builds everything under $W from the pinned repo, starts it under supervisord, prints READY.
# Idempotent: every step skips when its result exists, so rerun it after a reboot or a second start.
# Vast may show /workspace/bootstrap.log in its UI: never print secrets (all output goes through `mask`).
set -euo pipefail

# Pins (2026-10-17). Base image: vastai/pytorch:2.11.0-cuda-12.8.1-py312-24.04-2026-10-16 (README).
readonly UV_VERSION=0.13.1
readonly TAILSCALE_VERSION=1.104.1   # >= the release with `up --auth-key=file:` (cmd/tailscale/cli/up.go)
readonly CLAUDE_CODE_VERSION=2.1.292
readonly PY_BACKEND=3.11
readonly PY_LITELLM=3.12
readonly PY_VLLM=3.12

# Secrets never reach the log: every line of stdout/stderr is scrubbed of their values.
mask() {
  awk 'BEGIN { n = split("LITELLM_MASTER_KEY TS_AUTHKEY HF_TOKEN", k, " "); for (i = 1; i <= n; i++) v[i] = ENVIRON[k[i]] }
       { for (i = 1; i <= n; i++) if (v[i] != "") while ((p = index($0, v[i])) > 0) $0 = substr($0, 1, p - 1) "***" substr($0, p + length(v[i]))
         print; fflush() }'
}
exec > >(mask) 2>&1

STEP=start
TMPFILES=()   # secret-bearing temp files, removed on any exit
trap 'rc=$?; rm -rf "${TMPFILES[@]}"; [ "$rc" -eq 0 ] || echo "FAILED: $STEP (exit $rc)"' EXIT

: "${LITELLM_MASTER_KEY:?LITELLM_MASTER_KEY is required}"
W="${DATA_DIRECTORY:-/workspace}"; W="${W%/}"
REPO="$W/selfhost-ai"; BIN="$W/bin"; VENVS="$W/venvs"
LOG_DIR="$W/logs"; VOICES_DIR="$W/voices"; TS_STATE="$W/tailscale"
HF_HOME="$W/hf"; UV_CACHE_DIR="$W/uv-cache"; UV_PYTHON_INSTALL_DIR="$W/uv-python"
MODELS_MANIFEST="$REPO/models.yaml"
DEFAULT_VOICE="${DEFAULT_VOICE:-m_young_01}"
ENABLE_HUNYUAN="${ENABLE_HUNYUAN:-0}"
HUNYUAN_GPU_UTIL="${HUNYUAN_GPU_UTIL:-0.45}"
UV_PYTHON_PREFERENCE=only-managed
HUNYUAN_REPO=""; HUNYUAN_REVISION=""   # filled below when enabled; supervisord.conf interpolates them either way
BACKEND_LD_LIBRARY_PATH=""             # filled once the backend venv exists
export W REPO BIN VENVS LOG_DIR VOICES_DIR TS_STATE HF_HOME UV_CACHE_DIR UV_PYTHON_INSTALL_DIR UV_PYTHON_PREFERENCE \
  MODELS_MANIFEST DEFAULT_VOICE ENABLE_HUNYUAN HUNYUAN_GPU_UTIL HUNYUAN_REPO HUNYUAN_REVISION BACKEND_LD_LIBRARY_PATH
export PATH="$BIN:$PATH"
mkdir -p "$BIN" "$VENVS" "$LOG_DIR" "$VOICES_DIR" "$HF_HOME" "$UV_CACHE_DIR" "$UV_PYTHON_INSTALL_DIR" "$TS_STATE"

ts() { date +%s.%N; }
T_START=$(ts)
since() { awk -v a="$1" -v b="$(ts)" 'BEGIN{printf "%.1fs", b-a}'; }
phase() { # name function: runs one step, prints its duration
  STEP=$1; local t; t=$(ts)
  "$2"
  echo "phase $1: $(since "$t")"
}
wait_http() { # url label timeout_s
  local end=$((SECONDS + $3))
  until curl -fsS -o /dev/null "$1" 2>/dev/null; do
    [ $SECONDS -lt $end ] || { echo "timeout waiting for $2"; exit 1; }
    sleep 2
  done
}

BACKEND_PY="$VENVS/backend/bin/python"
SUPERVISORCTL=("$VENVS/tools/bin/supervisorctl" -c "$REPO/deploy/supervisord.conf")
tailscale_cli() { "$BIN/tailscale" --socket="$TS_STATE/tailscaled.sock" "$@"; }

apt_deps() {
  local missing=()
  command -v ffmpeg >/dev/null || missing+=(ffmpeg)
  command -v rsync >/dev/null || missing+=(rsync)
  [ ${#missing[@]} -eq 0 ] && return 0
  DEBIAN_FRONTEND=noninteractive apt-get update -qq
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends "${missing[@]}"
}

install_uv() {
  [ -x "$BIN/uv" ] && return 0
  local d; d=$(mktemp -d)
  curl -fsSL "https://github.com/astral-sh/uv/releases/download/$UV_VERSION/uv-x86_64-unknown-linux-gnu.tar.gz" | tar -xz -C "$d" --strip-components=1
  install -m 0755 "$d/uv" "$BIN/uv"; rm -rf "$d"
}

sync_venv() { # name python_version [extra uv args]
  local name=$1 py=$2; shift 2
  [ -x "$VENVS/$name/bin/python" ] || uv venv --python "$py" "$VENVS/$name"
  uv pip sync --python "$VENVS/$name/bin/python" "$@" "$REPO/locks/$name.txt"
}
venvs() {
  sync_venv backend "$PY_BACKEND" --index-strategy unsafe-best-match --extra-index-url https://download.pytorch.org/whl/cu128
  sync_venv litellm "$PY_LITELLM"
  sync_venv tools "$PY_LITELLM"   # supervisor only
  [ "$ENABLE_HUNYUAN" = "1" ] && sync_venv vllm "$PY_VLLM"
  local site; site=$("$BACKEND_PY" -c 'import site;print(site.getsitepackages()[0])')
  # CTranslate2 needs the cuDNN 9 / cuBLAS 12 libs bundled with the torch wheels; scoped to the backend only.
  BACKEND_LD_LIBRARY_PATH="$site/nvidia/cudnn/lib:$site/nvidia/cublas/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
  if [ "$ENABLE_HUNYUAN" = "1" ]; then
    HUNYUAN_REPO=$("$BACKEND_PY" "$REPO/scripts/fetch_models.py" --get hunyuan-mt-7b repo)
    HUNYUAN_REVISION=$("$BACKEND_PY" "$REPO/scripts/fetch_models.py" --get hunyuan-mt-7b revision)
  fi
  true
}

install_tailscale() {
  [ -x "$BIN/tailscaled" ] && [ -x "$BIN/tailscale" ] && return 0
  local d; d=$(mktemp -d)
  curl -fsSL "https://pkgs.tailscale.com/stable/tailscale_${TAILSCALE_VERSION}_amd64.tgz" | tar -xz -C "$d" --strip-components=1
  install -m 0755 "$d/tailscale" "$d/tailscaled" "$BIN/"; rm -rf "$d"
}

# Order: supervisord (with tailscaled) must run before `tailscale up`, so it starts right after the tools venv exists.
# Only tailscaled + gpu-logger autostart; backend/litellm/vllm are started later, after models and voices are ready.
start_supervisord() {
  if "${SUPERVISORCTL[@]}" pid >/dev/null 2>&1; then
    echo "supervisord already running"
  else
    "$VENVS/tools/bin/supervisord" -c "$REPO/deploy/supervisord.conf" </dev/null >/dev/null 2>&1
  fi
  local end=$((SECONDS + 30))
  until [ -S "$TS_STATE/tailscaled.sock" ]; do
    [ $SECONDS -lt $end ] || { echo "tailscaled socket did not appear"; exit 1; }
    sleep 1
  done
}

tailscale_up() {
  if ! tailscale_cli status >/dev/null 2>&1; then
    : "${TS_AUTHKEY:?TS_AUTHKEY is required for the first login}"
    ( umask 077; printf '%s' "$TS_AUTHKEY" > "$TS_STATE/authkey" )
    TMPFILES+=("$TS_STATE/authkey")
    timeout 120 "$BIN/tailscale" --socket="$TS_STATE/tailscaled.sock" up \
      --auth-key="file:$TS_STATE/authkey" --hostname=gpu-vast --accept-dns=false
    rm -f "$TS_STATE/authkey"
  fi
  echo "tailnet ip: $(tailscale_cli ip -4)"
}

fetch_models() { "$BACKEND_PY" "$REPO/scripts/fetch_models.py"; }

# The voice bank is uploaded after the box starts (rsync from the Mac), never stored in git.
wait_for_voices() {
  local verify=(env "VOICES_DIR=$VOICES_DIR" "$BACKEND_PY" "$REPO/scripts/voices.py" verify)
  until "${verify[@]}" >/dev/null 2>&1; do
    echo "waiting for voice bank: from the Mac run \`make vast-voices\`"
    sleep 30
  done
  "${verify[@]}"
}

start_services() {
  "${SUPERVISORCTL[@]}" start backend
  [ "$ENABLE_HUNYUAN" = "1" ] && "${SUPERVISORCTL[@]}" start vllm
  "${SUPERVISORCTL[@]}" start litellm
  true
}

wait_ready() {
  local t; t=$(ts)
  wait_http http://127.0.0.1:8001/ready backend 900
  echo "backend ready in $(since "$t")"
  if [ "$ENABLE_HUNYUAN" = "1" ]; then
    t=$(ts); wait_http http://127.0.0.1:8002/health hunyuan 900
    echo "hunyuan ready in $(since "$t")"
  fi
  t=$(ts); wait_http http://127.0.0.1:4000/health/liveliness litellm 120
  echo "litellm ready in $(since "$t")"
}

# Warm-up: one real request per model through LiteLLM. The key goes in a 0600 header file, not curl's argv.
warm_up() {
  local hdr wav t m
  hdr=$(umask 077; mktemp); wav=$(mktemp --suffix=.wav)
  printf 'Authorization: Bearer %s\n' "$LITELLM_MASTER_KEY" > "$hdr"
  TMPFILES+=("$hdr" "$wav")
  t=$(ts)
  curl -fsS -m 120 -H "@$hdr" -H 'Content-Type: application/json' \
    -d '{"model":"f5-tts-th","input":"สวัสดีครับ","voice":"default","response_format":"wav"}' \
    -o "$wav" http://127.0.0.1:4000/v1/audio/speech
  [ "$(head -c 4 "$wav")" = "RIFF" ] || { echo "warm-up: speech did not return RIFF"; exit 1; }
  echo "warm-up f5-tts-th $(since "$t")"
  for m in whisper-th whisper-zh; do
    t=$(ts)
    curl -fsS -m 120 -H "@$hdr" -F "file=@$wav" -F "model=$m" -F response_format=json \
      -o /dev/null http://127.0.0.1:4000/v1/audio/transcriptions
    echo "warm-up $m $(since "$t")"
  done
  if [ "$ENABLE_HUNYUAN" = "1" ]; then
    t=$(ts)
    curl -fsS -m 120 -H "@$hdr" -H 'Content-Type: application/json' \
      -d '{"model":"hunyuan-mt-7b","messages":[{"role":"user","content":"Translate to English: สวัสดี"}],"max_tokens":16,"temperature":0.7}' \
      -o /dev/null http://127.0.0.1:4000/v1/chat/completions
    echo "warm-up hunyuan-mt-7b $(since "$t")"
  fi
}

phase apt-deps apt_deps
phase uv install_uv
phase venvs venvs
phase tailscale-install install_tailscale
phase supervisord start_supervisord
phase tailscale-up tailscale_up
phase fetch fetch_models
phase voices wait_for_voices
phase start-services start_services
phase wait-ready wait_ready
phase warm-up warm_up

STEP=done
echo "cold start total: $(since "$T_START")"
echo READY

# Convenience for the owner's SSH sessions, after READY so it never delays serving; a failure here is only logged.
install_claude_code() {
  command -v claude >/dev/null && claude --version | grep -q "$CLAUDE_CODE_VERSION" && return 0
  curl -fsSL https://claude.ai/install.sh | DISABLE_AUTOUPDATER=1 bash -s "$CLAUDE_CODE_VERSION"
}
phase claude-code install_claude_code || echo "claude-code install failed (server unaffected)"
STEP=done
