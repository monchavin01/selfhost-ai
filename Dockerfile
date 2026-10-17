# One image, no weights (models are fetched into $HF_HOME at start).
# Base: vLLM image (CUDA 12.9 build; hunyuan FP8 on Ampere runs via vLLM's Marlin kernels). Needs host driver >= 575.
# Backend and LiteLLM get their own uv venvs so torch/numpy/fastapi pins cannot clash with vLLM's.
# Runs as root on purpose: Vast/RunPod start containers as root and mount volumes root-owned.
FROM vllm/vllm-openai:v0.31.0-cu129

COPY --from=ghcr.io/astral-sh/uv:0.13.1 /uv /usr/local/bin/uv

RUN apt-get update \
 && apt-get install -y --no-install-recommends curl ffmpeg libsndfile1 \
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

ENV HF_HOME=/data/hf LOG_DIR=/data/logs VOICES_DIR=/app/voices
EXPOSE 4000
ENTRYPOINT ["/app/scripts/start.sh"]
