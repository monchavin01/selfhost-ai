# ThaiDub GPU model server

Serves ThaiDub's AI models from one rented GPU (Vast.ai, RTX 3090 24 GB) behind LiteLLM's OpenAI-compatible API.
There is no custom Docker image. A private Vast template starts a stock Vast PyTorch image, clones this repo at a
git tag and runs [`scripts/bootstrap.sh`](scripts/bootstrap.sh). No weights are in the repo: models download at
first start into `/workspace` and are reused after that.

```
ThaiDub (Mac) ──Tailscale──► gpu-vast:4000  LiteLLM ─┬─► backend :8001  f5-tts-th, whisper-th, whisper-zh
                                            (key, limits) └─► vLLM    :8002  hunyuan-mt-7b   (ENABLE_HUNYUAN=1)
```

| model | endpoint | engine | weights |
|---|---|---|---|
| `f5-tts-th` | `POST /v1/audio/speech` | F5-TTS-THAI, nfe 10, dynamic batching | VIZINTZOR/F5-TTS-THAI + vocos |
| `whisper-th` | `POST /v1/audio/transcriptions` | faster-whisper fp16 | large-v3-turbo (CT2) |
| `whisper-zh` | `POST /v1/audio/transcriptions` | faster-whisper fp16 | large-v3 (CT2) |
| `hunyuan-mt-7b` | `POST /v1/chat/completions` | vLLM | tencent/Hunyuan-MT-7B-fp8 |

Exact repos and commits are pinned in [`models.yaml`](models.yaml). The API contract and design decisions are in
[`docs/plans/thaidub-gpu-server.md`](docs/plans/thaidub-gpu-server.md).

**Testing status:** only unit tests and static checks (`bash -n`, `py_compile`) have run, on the Mac. Nothing has
run on a GPU or in a container yet. The first rental is the real test.

## The Vast template `thaidub-gpu`

The owner keeps a private Vast template with these settings:

| setting | value |
|---|---|
| Base image | `vastai/pytorch:2.11.0-cuda-12.8.1-py312-24.04-2026-10-16` (Ubuntu 24.04, CUDA 12.8.1) |
| Launch mode | Interactive shell server, SSH |
| Disk | 100 GB |
| Machine filter | 1x RTX 3090 24 GB, Taiwan, verified, reliability >= 0.99 |
| Template env | `LITELLM_MASTER_KEY`, `TS_AUTHKEY`, `HF_TOKEN` (and `ENABLE_HUNYUAN=1` if wanted) |

`DATA_DIRECTORY=/workspace/` comes from the base image.

In the template's image field, replace `@vastai-automatic-tag` with the exact digest so the base never drifts:

```
vastai/pytorch:2.11.0-cuda-12.8.1-py312-24.04-2026-10-16@sha256:1b4c88782699223103da1aafcc92370dcc1691ef3c8f9907ff77c9db27b22869
```

That is the amd64 digest.

### On-start Script

Paste this exactly:

```
REPO=/workspace/selfhost-ai; TAG=v0.1.0
if [ -d "$REPO/.git" ]; then git -C "$REPO" fetch -q --depth 1 origin tag "$TAG" && git -C "$REPO" checkout -q -f "$TAG"; else git clone -q --depth 1 --branch "$TAG" https://github.com/monchavin01/selfhost-ai.git "$REPO"; fi
nohup bash "$REPO/scripts/bootstrap.sh" > /workspace/bootstrap.log 2>&1 &
entrypoint.sh
```

Why `entrypoint.sh` is the last line: Vast's base-image `entrypoint.sh` ends by `exec`ing `boot_default.sh`, so
any line after it may never run. Vast's template docs also say to call the original entrypoint at the end. Our
lines only start `bootstrap.sh` in the background, so they do not block Vast's own init.
Sources: `ROOT/opt/instance-tools/bin/entrypoint.sh` in github.com/vast-ai/base-image at commit `7a73f7b`, and
docs.vast.ai/guides/templates/template-settings.

Do not create `~/.no_auto_tmux`. Keep Vast's auto-tmux so your SSH sessions survive an iTerm disconnect.

### What `bootstrap.sh` does

1. Installs `ffmpeg` and `rsync` with apt if missing.
2. Installs uv 0.13.1 and builds the venvs from `locks/*.txt` (hash-pinned; `make lock` regenerates them).
3. Runs Tailscale 1.104.1 in userspace mode under supervisord: `tailscale up --auth-key=file:... --hostname gpu-vast`.
   The key is never in argv or logs.
4. Downloads the models listed in `models.yaml` into `/workspace/hf`.
5. Waits for the voice bank (see [Voice bank](#voice-bank)).
6. Starts backend, LiteLLM and (if enabled) vLLM under supervisord with autorestart, then runs a warm-up.
7. Prints `cold start total`, then `READY`.
8. Installs Claude Code 2.1.292 for SSH sessions.

It is idempotent: rerunning it or rebooting the box reuses everything under `/workspace`. Secrets are masked in the log.

### Reaching the box over Tailscale

In userspace mode `tailscaled` dials inbound tailnet TCP to `127.0.0.1:<port>`
([tailscale v1.104.1 `wgengine/netstack/netstack.go`, `acceptTCP`, `case isTailscaleIP: dialIP = ipv4Loopback`](https://github.com/tailscale/tailscale/blob/v1.104.1/wgengine/netstack/netstack.go#L1773)).
So LiteLLM on `:4000` is reachable at `gpu-vast:4000`.

- **Verified from source only, not yet tested on a box.** If it fails, use `make vast-tunnel` (SSH forward) instead.
- Backend `:8001` has no auth and is reachable from the tailnet too. If other people share your tailnet, restrict
  it with a Tailscale ACL.

### Hunyuan (optional)

Set `ENABLE_HUNYUAN=1` in the template env. vLLM 0.31.0 PyPI wheels are CUDA 13, so the host needs driver >= 580;
`scripts/vast.sh` then filters offers to `cuda >= 13.0`.

## Rent and run

1. **One-time on the Mac**
   - `uv tool install vastai`, then `vastai set api-key <key>`.
   - Add your SSH public key in the Vast console (applies to new instances only).
   - Put the Mac on the same tailnet (Tailscale app).
   - Create a reusable or ephemeral tagged Tailscale auth key and set it as `TS_AUTHKEY` in the template.
   - Run `make init`, then fill `.env`: `VAST_TEMPLATE_HASH`, `LITELLM_MASTER_KEY`, `LITELLM_URL=http://gpu-vast:4000`.

2. **Rent**
   - `make vast-up` needs the template hash. It rents the cheapest matching offer, uploads the voices and waits for READY.
   - Or rent from the web console with the template, then run `make vast-voices`.

   ```bash
   make vast-up
   ```

3. **Watch the log over SSH**
   - `make vast-logs`, or from iTerm: `ssh -p <port> root@<host>` then `tail -f /workspace/bootstrap.log`.
   - Service logs are in `/workspace/logs/*.log`.
   - On the box, `status.sh` shows supervisor, health, GPU and recent requests:

   ```bash
   LITELLM_MASTER_KEY=... /workspace/selfhost-ai/scripts/status.sh
   ```

4. **Check from the Mac** (on the tailnet, with `$LITELLM_MASTER_KEY` in your environment)

   LiteLLM is alive:

   ```bash
   curl -s http://gpu-vast:4000/health/liveliness
   ```

   A wrong key gives `401`:

   ```bash
   curl -s -o /dev/null -w '%{http_code}\n' -H "Authorization: Bearer wrong" http://gpu-vast:4000/v1/models
   ```

   Speech:

   ```bash
   curl -s http://gpu-vast:4000/v1/audio/speech \
     -H "Authorization: Bearer $LITELLM_MASTER_KEY" -H 'Content-Type: application/json' \
     -d '{"model":"f5-tts-th","input":"สวัสดีครับ","voice":"default","response_format":"wav"}' \
     -o /tmp/t.wav
   ```

   It should print `RIFF`:

   ```bash
   head -c 4 /tmp/t.wav
   ```

   Thai transcription:

   ```bash
   curl -s http://gpu-vast:4000/v1/audio/transcriptions \
     -H "Authorization: Bearer $LITELLM_MASTER_KEY" \
     -F file=@/tmp/t.wav -F model=whisper-th -F language=th -F temperature=0 -F response_format=json
   ```

   Chinese transcription with segment and word timestamps (prints both counts):

   ```bash
   curl -s http://gpu-vast:4000/v1/audio/transcriptions \
     -H "Authorization: Bearer $LITELLM_MASTER_KEY" \
     -F file=@/tmp/t.wav -F model=whisper-zh -F response_format=verbose_json \
     -F 'timestamp_granularities[]=segment' -F 'timestamp_granularities[]=word' \
     | python3 -c 'import sys,json; d=json.load(sys.stdin); print("segments", len(d.get("segments", [])), "words", len(d.get("words", [])))'
   ```

5. **Point ThaiDub at it**, in its `.env`:

   ```
   LITELLM_BASE_URL=http://gpu-vast:4000/v1
   LITELLM_API_KEY=<LITELLM_MASTER_KEY>
   ```

6. **Destroy and recreate**
   - Note the voice bank hash: `curl -s http://gpu-vast:8001/voices-manifest`.
   - Run `make vast-down` (destroys the rental and its disk, after you type the instance id), then rent again.
   - The same tag gives the same code, models and bank hash.
   - Cold start downloads everything again unless a Vast volume is mounted at `/workspace`. A volume costs storage
     while idle.
   - To upgrade, push a new git tag and change `TAG` in the On-start Script.

Other commands:

```bash
make vast-offers   # 10 cheapest matching offers
```

```bash
make vast-ssh      # shell on the rental
```

```bash
make vast-tunnel   # fallback: forward :4000 and :8001 over SSH (then use http://localhost:4000)
```

The rental id is kept in `.vast-instance` (never commit it).

## Agent rules

[`CLAUDE.md`](CLAUDE.md) holds the rules for AI agents working in this repo. ponytail full (smallest complete
change) and caveman full (terse replies) are on by default through `.claude/settings.json`, which enables the
plugins from GitHub `DietrichGebert/ponytail` and `juliusbrussee/caveman`. Their hooks need node.

## Check and measure

On the box: `status.sh` as shown in [Rent and run](#rent-and-run), step 3.

From the Mac, on the tailnet (or through `make vast-tunnel` with `LITELLM_URL=http://localhost:4000`):

```bash
LITELLM_MASTER_KEY=<key> make bench    # F5 1/4/8 concurrent, contention, VRAM per model, contract checks
```

`python3 scripts/bench.py --only speech|contention|vram|contract` runs one section.

Logs live in `/workspace/logs`:
- `gpu.log`: utilisation %, VRAM used/total and temperature, every 5 s.
- `requests.log`: model, duration and status per request. It holds no bodies and no keys.

## Voice bank

The clips are cut from real dubbed audio, so they are **never in git** (this repo is public). They live in your
local `voices/` folder; only `voices/manifest.sha256` and `voices/README.md` are tracked. `make vast-up` uploads the
folder to `/workspace/voices` on the box with rsync, and the server waits at start until the bank matches the manifest.

To add or change a voice:

1. Put `<voice_id>.neutral.th.wav` (24 kHz mono, at most 15 s) and `<voice_id>.neutral.th.txt` (the exact words
   spoken) in `voices/`. The `voice_id` is the file name before the first `.`.
2. Rebuild the manifest, then commit it (only the manifest is committed):

   ```bash
   make voices
   ```

3. Upload to a running rental:

   ```bash
   make vast-voices
   ```

The server refuses any voice whose files don't match the manifest. It reads the bank at start, so restart the
services (or recreate the rental) after changing voices.

## Configuration

Server settings come from the Vast template env (`LITELLM_MASTER_KEY`, `TS_AUTHKEY`, `HF_TOKEN`, optional
`ENABLE_HUNYUAN`, `DEFAULT_VOICE`, `F5_*`). Mac-side settings (`VAST_TEMPLATE_HASH`, `LITELLM_URL`, ...) live in
`.env`. See [`.env.example`](.env.example). Secrets are never in git and never in logs.

## Unit tests

```bash
make test          # backend logic only: no GPU, no model weights
```
