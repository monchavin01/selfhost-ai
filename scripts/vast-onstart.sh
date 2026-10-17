#!/usr/bin/env bash
# Vast on-start command (SSH launch mode replaces the image entrypoint, so this starts the server).
# Vast shows template/account env vars only to this script, so it hands the ones we need to SSH sessions
# (status.sh, bench, claude) via /etc/environment, then starts the server in the background.
set -euo pipefail

SHARED_VARS=(LITELLM_MASTER_KEY HF_TOKEN HF_HOME LOG_DIR DEFAULT_VOICE ENABLE_HUNYUAN
             F5_BATCH_MAX F5_BATCH_WAIT_MS F5_DEFAULT_SEED CLAUDE_CODE_OAUTH_TOKEN ANTHROPIC_API_KEY
             DISABLE_AUTOUPDATER PATH)
umask 077  # /etc/environment holds keys: root-only
for name in "${SHARED_VARS[@]}"; do
  if [ -n "${!name:-}" ]; then
    grep -v "^${name}=" /etc/environment > /etc/environment.new 2>/dev/null || true
    printf '%s=%s\n' "$name" "${!name}" >> /etc/environment.new
    mv /etc/environment.new /etc/environment
  fi
done

mkdir -p /data
nohup /app/scripts/start.sh > /data/start.log 2>&1 &
echo "server starting; follow with: tail -f /data/start.log"
