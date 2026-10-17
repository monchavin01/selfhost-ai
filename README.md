# ThaiDub GPU model server

Serves ThaiDub's AI models from one rented GPU (Vast.ai RTX 3090 24 GB; RunPod as fallback) behind LiteLLM's
OpenAI-compatible API. One Docker image, no weights inside: models download into a cache at first start and are
reused after that.

```
ThaiDub (Mac) ──ssh -L 4000──► LiteLLM :4000 ─┬─► backend :8001  f5-tts-th, whisper-th, whisper-zh
                               (key, limits)  └─► vLLM    :8002  hunyuan-mt-7b   (ENABLE_HUNYUAN=1)
```

| model | endpoint | engine | weights |
|---|---|---|---|
| `f5-tts-th` | `POST /v1/audio/speech` | F5-TTS-THAI, nfe 10, dynamic batching | VIZINTZOR/F5-TTS-THAI + vocos |
| `whisper-th` | `POST /v1/audio/transcriptions` | faster-whisper fp16 | large-v3-turbo (CT2) |
| `whisper-zh` | `POST /v1/audio/transcriptions` | faster-whisper fp16 | large-v3 (CT2) |
| `hunyuan-mt-7b` | `POST /v1/chat/completions` | vLLM | tencent/Hunyuan-MT-7B-fp8 |

Exact repos and commits are pinned in [`models.yaml`](models.yaml). The API contract and design decisions are in
[`docs/plans/thaidub-gpu-server.md`](docs/plans/thaidub-gpu-server.md).

## Rent and run on Vast.ai

1. Build and push the image (from any machine with Docker; the Mac is not needed). This makes two tags:
   - `<tag>`: F5 + whisper only. It is small, so a fresh rental pulls it fast. Use it by default.
   - `<tag>-hunyuan`: adds vLLM for `ENABLE_HUNYUAN=1`. It is much larger.

   ```bash
   make build push IMAGE=ghcr.io/<you>/thaidub-gpu-server:<tag>
   ```

2. Create a Vast template:
   - Image: `<tag>`, or `<tag>-hunyuan` when you also set `ENABLE_HUNYUAN=1`. Launch mode **SSH** (gives you `ssh` for the tunnel).
   - On-start script: `nohup /app/scripts/start.sh > /data/start.log 2>&1 &`
     (SSH mode replaces the image entrypoint, so the onstart script starts the server.)
   - Env: `-e LITELLM_MASTER_KEY=<key>` and optionally `-e ENABLE_HUNYUAN=1`, `-e HF_TOKEN=...`,
     `-e DEFAULT_VOICE=<voice_id>` (image default `m_young_01`).
   - Disk: **40 GB** for the light tag, **60 GB** for hunyuan. Weights are about 6 GB, or about 14 GB with hunyuan.
     Vast fixes disk size at creation, so leave headroom.
   - GPU: RTX 3090 24 GB. The image needs host driver >= 575 (CUDA 12.9), so filter offers by driver version.
3. Rent an offer with the template, then wait for READY:

   ```bash
   ssh -p <port> root@<host> 'tail -f /data/start.log'
   ```

   It prints the time for each phase (`phase fetch`, `phase load`, `phase warm-up`), then `cold start total`, then
   `READY`. `cold start total` starts after Vast has pulled the image; read the pull time from the Vast console.
4. Open the tunnel from the Mac and point ThaiDub at it:

   ```bash
   ssh -N -L 4000:127.0.0.1:4000 -L 8001:127.0.0.1:8001 -p <port> root@<host>
   ```

   ThaiDub `.env`: `LITELLM_BASE_URL=http://localhost:4000/v1` and `LITELLM_API_KEY=<key>`. Port 8001 (backend,
   no auth) is only for `make bench` / the voices check and stays on the tunnel.
5. Confirm it is the same voice bank as last time. The `sha256` must match the previous rental:

   ```bash
   curl -s localhost:8001/voices-manifest      # through the tunnel
   ```

### Destroy and recreate

Instance disk is deleted on destroy, so the next rental downloads the weights again (cold start). To skip that, keep
the cache on a provider volume:

- **Vast:** create a volume, mount it at `/workspace`, and set `-e HF_HOME=/workspace/hf`. A volume costs storage
  while no instance runs. Vast lists volumes per machine, so a recreated instance must be on the same machine to
  reuse it. That last point is inferred from the docs; check it before relying on it.
- **RunPod (fallback, not yet tried):** network volumes (Secure Cloud only) mount at `/workspace`, so set
  `HF_HOME=/workspace/hf`. Pods use the image's start command and env vars from the template. Open item: the image
  has no sshd, and RunPod's SSH into a custom image still needs checking before the tunnel works there.

Rule of thumb: rent briefly and rarely, and let the instance disk re-download; rent daily, and use a volume. Steps:

1. Note the voices `sha256` (step 5 above).
2. Destroy the instance.
3. Rent a new one from the same template, wait for `READY`, and re-open the tunnel.
4. Check the `sha256` matches.

## Check and measure

On the instance (SSH sessions do not see the template env vars, so pass the key):

```bash
LITELLM_MASTER_KEY=<key> /app/scripts/status.sh    # LiteLLM /health, last GPU lines, last requests, voices hash
```

From the Mac, through the tunnel (both ports forwarded):

```bash
LITELLM_MASTER_KEY=<key> make bench    # F5 1/4/8 concurrent, contention, VRAM per model, contract checks
```

`python3 scripts/bench.py --only speech|contention|vram|contract` runs one section.

Logs live in `$LOG_DIR` (default `/data/logs`):
- `gpu.log`: utilisation %, VRAM used/total and temperature, every 5 s.
- `requests.log`: model, duration and status per request. It holds no bodies and no keys.

### Testing the image on a local NVIDIA box

```bash
make init          # creates .env — set LITELLM_MASTER_KEY
make build run     # docker run with --gpus all --cpus 8 --memory 24g (override CPUS=, MEMORY=)
```

## Add a voice

1. Put `<voice_id>.neutral.th.wav` (24 kHz mono, at most 15 s) and `<voice_id>.neutral.th.txt` (the exact words
   spoken) in `voices/`. The `voice_id` is the file name before the first `.`.
2. Run `make voices` to rebuild `voices/manifest.sha256`, then commit.
3. Rebuild and push the image, then recreate the rental.

The server refuses any voice whose files don't match the manifest.

## Configuration

All settings come from env vars. See [`.env.example`](.env.example). Secrets (`LITELLM_MASTER_KEY`, `HF_TOKEN`) are
never in git and never in logs.

## Unit tests

```bash
make test          # backend logic only: no GPU, no model weights
```
