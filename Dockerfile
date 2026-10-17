# One Dockerfile, two tags, no weights (models are fetched into $HF_HOME at start):
#   light (default)  BASE_IMAGE=ubuntu:24.04 — F5 + whisper only; small, so a fresh rental pulls it fast.
#   hunyuan          BASE_IMAGE=vllm/vllm-openai:v0.31.0-cu129 — adds vLLM for ENABLE_HUNYUAN=1 (FP8 on Ampere via
#                    vLLM's Marlin kernels).
# CUDA libs come from the torch cu128 wheels, so the light base needs none; host driver must be >= 570 (CUDA 12.8).
# The -hunyuan tag's vLLM base is a CUDA 12.9 build: host driver >= 575 (scripts/vast.sh filters offers for it).
# Backend and LiteLLM get their own uv venvs so torch/numpy/fastapi pins cannot clash with vLLM's.
# Runs as root on purpose: Vast/RunPod start containers as root and mount volumes root-owned.
ARG BASE_IMAGE=ubuntu:24.04
FROM ${BASE_IMAGE}
# Plain bases don't ask the NVIDIA runtime for nvidia-smi (the GPU logger needs it); the vLLM base already does.
ENV NVIDIA_VISIBLE_DEVICES=all NVIDIA_DRIVER_CAPABILITIES=compute,utility

COPY --from=ghcr.io/astral-sh/uv:0.13.1 /uv /usr/local/bin/uv
# node runs the ponytail/caveman Claude Code plugin hooks; apt's node is too old on the vLLM (ubuntu 22.04) base.
COPY --from=node:22.23.3-bookworm-slim /usr/local/bin/node /usr/local/bin/node

# openssh-server + tmux: Vast/RunPod SSH sessions; rsync: voice bank upload (never in the image or git); git/less/vim: working on the box with Claude Code.
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl ffmpeg libsndfile1 \
      openssh-server rsync tmux git less vim-tiny \
 && rm -rf /var/lib/apt/lists/*

# Claude Code CLI, pinned (stable channel on 2026-10-17); updates come from rebuilding the image, not at runtime.
ENV DISABLE_AUTOUPDATER=1 PATH=/root/.local/bin:$PATH
RUN curl -fsSL https://claude.ai/install.sh | bash -s 2.1.292 && claude --version

# Backend venv. torch from the cu128 index (wheels bundle cuDNN 9 + cuBLAS 12 that CTranslate2 4.x needs;
# start.sh puts them on LD_LIBRARY_PATH for the backend only). backend/requirements.txt must pin
# f5-tts-th==1.0.9 and faster-whisper==1.2.1 (f5-tts-th requires numpy<=1.26.4).
COPY backend/requirements.txt /tmp/backend-requirements.txt
RUN uv venv --python 3.11 /opt/backend \
 && uv pip install --python /opt/backend/bin/python \
      --extra-index-url https://download.pytorch.org/whl/cu128 \
      torch==2.11.0 torchaudio==2.11.0 -r /tmp/backend-requirements.txt \
 && rm /tmp/backend-requirements.txt

RUN uv venv --python 3.12 /opt/litellm \
 && uv pip install --python /opt/litellm/bin/python "litellm[proxy]==1.105.1"

WORKDIR /app
COPY backend /app/backend
COPY scripts /app/scripts
COPY models.yaml litellm.yaml CLAUDE.md /app/
# Agent rules + plugin settings, so `cd /app && claude` starts with the repo's rules and ponytail/caveman.
COPY .claude/settings.json /app/.claude/settings.json

# /data must exist: the Vast onstart line redirects into it before start.sh runs.
RUN mkdir -p /data/hf /data/logs
ENV HF_HOME=/data/hf LOG_DIR=/data/logs VOICES_DIR=/data/voices DEFAULT_VOICE=m_young_01
EXPOSE 4000
ENTRYPOINT ["/app/scripts/start.sh"]
