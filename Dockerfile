# One Dockerfile, two tags, no weights (models are fetched into $HF_HOME at start):
#   light (default)  BASE_IMAGE=ubuntu:24.04 — F5 + whisper only; small, so a fresh rental pulls it fast.
#   hunyuan          BASE_IMAGE=vllm/vllm-openai:v0.31.0-cu129 — adds vLLM for ENABLE_HUNYUAN=1 (FP8 on Ampere via
#                    vLLM's Marlin kernels).
# CUDA libs come from the torch cu129 wheels, so the light base needs none; host driver must be >= 575 (CUDA 12.9).
# Backend and LiteLLM get their own uv venvs so torch/numpy/fastapi pins cannot clash with vLLM's.
# Runs as root on purpose: Vast/RunPod start containers as root and mount volumes root-owned.
ARG BASE_IMAGE=ubuntu:24.04
FROM ${BASE_IMAGE}
# Plain bases don't ask the NVIDIA runtime for nvidia-smi (the GPU logger needs it); the vLLM base already does.
ENV NVIDIA_VISIBLE_DEVICES=all NVIDIA_DRIVER_CAPABILITIES=compute,utility

COPY --from=ghcr.io/astral-sh/uv:0.13.1 /uv /usr/local/bin/uv

RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl ffmpeg libsndfile1 \
 && rm -rf /var/lib/apt/lists/*

# Backend venv. torch from the cu129 index (wheels bundle cuDNN 9 + cuBLAS 12 that CTranslate2 4.x needs;
# start.sh puts them on LD_LIBRARY_PATH for the backend only). backend/requirements.txt must pin
# f5-tts-th==1.0.9 and faster-whisper==1.2.1 (f5-tts-th requires numpy<=1.26.4).
COPY backend/requirements.txt /tmp/backend-requirements.txt
RUN uv venv --python 3.11 /opt/backend \
 && uv pip install --python /opt/backend/bin/python \
      --extra-index-url https://download.pytorch.org/whl/cu129 \
      torch==2.11.0 torchaudio==2.11.0 -r /tmp/backend-requirements.txt \
 && rm /tmp/backend-requirements.txt

RUN uv venv --python 3.12 /opt/litellm \
 && uv pip install --python /opt/litellm/bin/python "litellm[proxy]==1.105.1"

WORKDIR /app
COPY backend /app/backend
COPY voices /app/voices
COPY scripts /app/scripts
COPY models.yaml litellm.yaml /app/

# /data must exist: the Vast onstart line redirects into it before start.sh runs.
RUN mkdir -p /data/hf /data/logs
ENV HF_HOME=/data/hf LOG_DIR=/data/logs VOICES_DIR=/app/voices DEFAULT_VOICE=m_young_01
EXPOSE 4000
ENTRYPOINT ["/app/scripts/start.sh"]
