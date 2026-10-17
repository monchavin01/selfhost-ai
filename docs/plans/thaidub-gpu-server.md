# Plan: ThaiDub GPU server (home 4070 Ti + Vast 3090)

Status: draft for owner review, 2026-10-17. Nothing built yet.

## Owner answers (2026-10-17)

- Placement: reshape this coding-stack repo itself into the ThaiDub server (not a side directory).
- Backend: rewrite from scratch (F5-TTS + faster-whisper); old hand-run code is not used.
- Target host first: Vast RTX 3090 24 GB, reached over an SSH tunnel. All models (incl. hunyuan) fit; home
  16 GB card VRAM split is no longer a blocker.
- Hunyuan temperature: 0.7 per contract (client real-4 default 0.2 must be changed on the ThaiDub side).
- Repo: modify this directory in place to serve ThaiDub. Coding-stack-only parts (vLLM/llama.cpp profiles,
  admin-api, profile guard, Postgres, Langfuse) are removed; recoverable from git at 680a59c.
- Network: SSH tunnel only (`ssh -L 4000:localhost:4000`). No Tailscale in the image, no TS_AUTHKEY.

## What I read (sources)

- This repo (`selfhost-ai`, branch `claude/litellm-thaidub-gpu-server-959903`): LiteLLM gateway + vLLM/llama.cpp
  coding profiles, admin-api, Postgres, Langfuse. **No F5 / Whisper / voice code.**
- Home GPU server: unreachable from this Mac at planning time (SSH timed out).
  The F5/Whisper backend that "runs today by hand" was therefore **not read**.
- ThaiDub client, branch `real-4` (read via `git show`, not checked out):
  - speech: `src/thaidub/tts/f5_server.py:81-111` — sends `speed` only if != 1.0, `seed` only if `send_seed`
    (default False).
  - heard check: `src/thaidub/tts/intelligibility.py` (whisper-th, json).
  - ASR 2nd opinion: `src/thaidub/asr/providers.py` `ServerWhisperASR` (whisper-zh, verbose_json, segment+word).
  - Hunyuan: `src/thaidub/translate/litellm_mt.py:40` sends `self.temperature`; default 0.2 at
    `src/thaidub/translate/llm.py:398` (contract says 0.7).
  - 429: `src/thaidub/qwen_cloud.py:188-222` waits Retry-After, cap 60 s per wait, 120 s total.
  - No multi-server spreading in client code yet.
- Voice bank: not in git. Latest used bank `runs/preset-bank-f5-20261016/bank/` (11 neutral wav + txt, 6.4 MB).
  No manifest, no `default` voice in it.
- F5 weights on Mac: `models/f5-tts-thai/model_1000000.pt` (1.35 GB), `vocab.txt`, `vocos-mel-24khz/`.
- Docs (via research subagent, summarised pages — re-check before relying on them):
  - Vast: docker-in-docker disabled (docs.vast.ai FAQ technical); SSH/Jupyter launch modes replace the image
    entrypoint, use onstart script; disk size fixed at creation.
  - RunPod: no docker-in-docker, no compose, TCP/HTTP only (docs.runpod.io/pods/overview).
  - F5-TTS `infer_batch_process` takes one `ref_audio`/`ref_text` per call (SWivid/F5-TTS `utils_infer.py`);
    official Triton+TRT-LLM runtime has `dynamic_batching`, `max_batch_size: 4`.
  - faster-whisper: `BatchedInferencePipeline`, `word_timestamps=True`.

## Design

```
container (one image, no weights)
  start.sh  -> tailscaled --tun=userspace-networking (hostname $TS_HOSTNAME)
            -> fetch models per models.yaml into $HF_HOME (repo + revision pinned)
            -> verify voices/manifest.sha256
            -> backend (FastAPI, :8001)   F5 + whisper-th + whisper-zh [+ hunyuan if ENABLE_HUNYUAN=1]
            -> litellm (:4000)            auth, rpm / max_parallel limits, 429 Retry-After, per-request log
            -> gpu-log loop (nvidia-smi every 5 s)
            -> warm-up request per model -> print READY
```

New directory `thaidub-server/` (self-contained):

| file | job |
|---|---|
| `Dockerfile` | CUDA runtime + Python deps + litellm + tailscale. No weights. |
| `models.yaml` | one manifest: repo, revision (commit hash), files, per-model enable flag |
| `start.sh` | idempotent; image CMD and Vast onstart both call it |
| `backend/app.py` | OpenAI-shaped `/v1/audio/speech`, `/v1/audio/transcriptions`, `/voices-manifest` |
| `litellm.yaml` | model routes, rpm / max_parallel_requests, hunyuan defaults (top_k 20, top_p 0.6, rep 1.05) |
| `voices/` + `voices/manifest.sha256` | bank; mismatched voice refused |
| `compose.yaml` | home only: same image, `cpus`, `mem_limit`, GPU, cache volume |
| `status.sh` / `make status` | /health, last GPU lines, last request lines |
| `README.md` | Vast rent-and-run, home steps, add-a-voice |

Not in scope: Grafana/Prometheus, autoscaling, multi-cloud layer, Postgres/Langfuse for this server.

## Decisions (trade-offs)

1. **Placement** — new self-contained dir in this repo (recommended) vs separate repo vs merged into coding-stack
   compose. Vast/RunPod cannot run compose, so the coding-stack services cannot come along anyway.
2. **LiteLLM in front vs backend-only** — prompt wants LiteLLM. Risk: LiteLLM may drop `seed` on speech, mangle
   `timestamp_granularities[]` / `language=""`, or not send `Retry-After`. Step 2 tests this against a stub backend
   first; fallback is a LiteLLM pass-through route for the failing endpoint.
3. **Same voice on both servers** — client does not send seed by default, so backend uses a fixed default seed
   (e.g. 0) when none is sent. Bit-identical audio across a 3090 and a 4070 Ti is not expected (different kernels);
   acceptance uses speaker-embedding cosine as the prompt says.
4. **F5 batching** — spike with go/no-go, not a promise:
   a. measure GPU util at concurrency 1. If near 100 %, batching cannot help much.
   b. if low: queue + collect up to 4 requests within ~20 ms, pad conditioning (ref mel + text) and run one
      `model.sample()` with batch > 1 (F5 sampler supports batch; `infer_batch_process` does not).
   c. fallback: official Triton/TRT-LLM runtime (heavier image, separate engine build per GPU arch).
5. **Whisper** — faster-whisper (CTranslate2 fp16), unless the existing server has better.
6. **VRAM on 16 GB home card** — F5 (~2-3 GB) + whisper-th + whisper-zh (large-v3 fp16 ~3-4 GB each, int8 less)
   fits only with the coding profiles stopped (`make off`). Hunyuan only when `ENABLE_HUNYUAN=1` (24 GB card).

## Steps (each small, reversible, one commit)

1. Skeleton: Dockerfile, models.yaml, start.sh (download + cache reuse + timing), stub backend returning contract
   shapes. Acceptance: empty-cache start vs warm start timed.
2. LiteLLM front: routes, keys, limits. Prove 401, 429 + Retry-After, seed / `timestamp_granularities[]` /
   `language=""` pass through (stub echoes).
3. Real backend: port existing F5 + Whisper code (needs Q1), fixed default seed, RIFF/24 kHz/mono/16-bit check.
4. Voice bank + manifest.sha256 + `/voices-manifest`; refuse mismatched voice.
5. Tailscale userspace + inbound check from the Mac.
6. Monitoring: GPU log, per-request log (no bodies, no keys), `make status`.
7. Batching spike (decision 4), report 1 vs 4 concurrent.
8. Hunyuan behind `ENABLE_HUNYUAN` (Vast only), measured VRAM.
9. Two-instance test (same manifest hash, cosine), README, Vast run.

## Open questions

1. **Where is the F5/Whisper backend code running today on the home server?** Not in this repo or ThaiDub. Please
   commit + push it (or copy it here) so step 3 ports it instead of rewriting, and so the 1.8x slowdown is
   diagnosed on the real code.
2. Home server reachable how? (LAN from this Mac times out; Tailscale stopped.) Needed for every acceptance run.
3. Hunyuan temperature: client default 0.2 vs contract 0.7 — which?
4. whisper-th / whisper-zh: exact HF repo + revision? (Only F5 is pinned in the prompt.)
5. Canonical voice bank: `preset-bank-f5-20261016` (11 voices)? Which clip becomes `default`?
6. Home: stop coding profiles while ThaiDub server runs, or keep both (then whisper must go int8 / smaller)?
7. Vast: docker image registry to push to (GHCR under `monchavin01`?).

## Interfaces (fixed before parallel build — every piece codes against this)

Layout (repo root after the coding stack is removed):

```
Dockerfile            image: code + deps, no weights
compose.yaml          home GPU only: same image, cpus/mem_limit, GPU, ./cache + ./logs volumes
models.yaml           model manifest (format below)
litellm.yaml          LiteLLM routes + limits
scripts/start.sh      container entrypoint (also the Vast onstart command)
scripts/fetch_models.py   download models.yaml entries into $HF_HOME (pinned revision), print progress
scripts/voices.py     `build` writes voices/manifest.sha256, `verify` checks it (exit 1 on mismatch)
scripts/status.sh     `make status`: /health, last GPU lines, last request lines
backend/              FastAPI app (package `backend`), tests in backend/tests/
voices/               <voice_id>.neutral.th.wav + .txt, manifest.sha256
```

Processes inside the container (started by scripts/start.sh, in this order):

| process | listens | notes |
|---|---|---|
| backend (uvicorn `backend.app:app`) | 127.0.0.1:8001 | F5 + whisper; `GET /ready` 200 once all enabled models loaded |
| vLLM hunyuan (only `ENABLE_HUNYUAN=1`) | 127.0.0.1:8002 | OpenAI server, served name `hunyuan-mt-7b` |
| LiteLLM | 0.0.0.0:4000 | only port reached from outside (SSH tunnel) |
| gpu logger | — | `nvidia-smi` every 5 s to `$LOG_DIR/gpu.log` |

Backend HTTP (OpenAI shapes, no auth — LiteLLM in front holds the key):
- `POST /v1/audio/speech` JSON `{model, input, voice, response_format:"wav", speed?, seed?}` → WAV RIFF mono 24 kHz
  PCM16. Unknown or hash-mismatched voice → 400 / 404 with JSON error. No seed → `F5_DEFAULT_SEED`.
- `POST /v1/audio/transcriptions` multipart `{file, model, language?, temperature?, prompt?, response_format,
  timestamp_granularities[]?}`; `language=""` = auto-detect. `json` → `{"text"}`; `verbose_json` →
  `{"text","language","duration","segments":[{"id","start","end","text",...}],"words":[{"start","end","word"}]}`.
  `model` picks the whisper model by its models.yaml name (`whisper-th`, `whisper-zh`).
- `GET /voices-manifest` → `{"sha256": <hash of voices/manifest.sha256>, "voices": [ids], "refused": [ids]}`.
- `GET /ready` → 200 `{"models": [...]}` when loaded, 503 before.
- Per-request log line to `$LOG_DIR/requests.log`: ISO time, model, duration_ms, status. Never bodies or keys.

Environment (all optional except LITELLM_MASTER_KEY):

| var | default | used by |
|---|---|---|
| LITELLM_MASTER_KEY | — (required) | LiteLLM |
| HF_TOKEN | empty | fetch_models.py |
| HF_HOME | /data/hf | fetch + backend + vLLM |
| LOG_DIR | /data/logs | everything |
| VOICES_DIR | /app/voices | backend, voices.py |
| ENABLE_HUNYUAN | 0 | start.sh, fetch_models.py |
| F5_BATCH_MAX | 4 | backend |
| F5_BATCH_WAIT_MS | 15 | backend |
| F5_DEFAULT_SEED | 0 | backend |
| DEFAULT_VOICE | (see open question 5) | backend: `voice="default"` maps to this voice_id |

models.yaml format:

```yaml
models:
  f5-tts-th:
    repo: VIZINTZOR/F5-TTS-THAI
    revision: af023c707f4208e5fc1fce59c1fd1501cfc3fc22
    files: [model_1000000.pt, vocab.txt]
  vocos-mel-24khz:
    repo: charactr/vocos-mel-24khz
    revision: 0feb3fdd929bcd6649e0e7c5a688cf7dd012ef21
  whisper-th:
    repo: deepdml/faster-whisper-large-v3-turbo-ct2
    revision: 4df90f75321148c3a29a9e2351b7ddf8f5b115a8
  whisper-zh:
    repo: Systran/faster-whisper-large-v3
    revision: edaa852ec7e145841d8ffdb056a99866b5f0a478
  hunyuan-mt-7b:
    repo: tencent/Hunyuan-MT-7B-fp8
    revision: 81e5a3f7199524570ba75e61360e990ba88665e4
    enabled_by: ENABLE_HUNYUAN
```

`files` absent = whole repo. Code reads the local path with
`huggingface_hub.snapshot_download(repo, revision=..., allow_patterns=files, local_files_only=True)`.
