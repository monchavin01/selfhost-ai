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

## Image tags

`make build push IMAGE=ghcr.io/<you>/thaidub-gpu-server:<tag>` builds and pushes two tags:

- `<tag>` (light): ubuntu base, F5 + whisper only. It is small, so a fresh rental pulls it fast. Use it by default.
- `<tag>-hunyuan`: vLLM base, adds `hunyuan-mt-7b` for `ENABLE_HUNYUAN=1`. It is much larger.

CUDA comes from the torch cu128 wheels, so the host driver must be >= 570 (CUDA 12.8). The hunyuan tag uses a
vLLM `-cu129` base and needs driver >= 575 (CUDA 12.9).

## One-time setup (Mac)

1. Install the Vast CLI and set your API key:

   ```bash
   pip install vastai
   ```

   ```bash
   vastai set api-key <key>
   ```

2. Add your SSH public key in the Vast console under Account > SSH Keys. It applies to new instances only.
3. Store secrets as Vast account env vars. Vast keeps them encrypted and injects them into every container:

   ```bash
   vastai create env-var LITELLM_MASTER_KEY <key>
   ```

   Optional: `HF_TOKEN`, and one of `CLAUDE_CODE_OAUTH_TOKEN` or `ANTHROPIC_API_KEY` for
   [Claude Code on the box](#working-on-the-box-with-claude-code). Create the OAuth token with
   `claude setup-token` on the Mac (needs a paid Claude plan).
4. Build and push both image tags (see [Image tags](#image-tags)). Run this on any machine with Docker and enough
   disk, not on the Mac:

   ```bash
   make build push IMAGE=ghcr.io/<you>/thaidub-gpu-server:<tag>
   ```

5. Create `.env` and edit `IMAGE`, `ENABLE_HUNYUAN` and `DEFAULT_VOICE`:

   ```bash
   make init
   ```

## Rent in one command

Minimum spec used by every `vast-*` command:

| requirement | value |
|---|---|
| GPU | RTX 3090 24 GB x 1 |
| host | verified, reliability >= 0.99 |
| CUDA | >= 12.8 (>= 12.9 automatically when `ENABLE_HUNYUAN=1`, because the vLLM base needs driver >= 575) |
| CPU / RAM | >= 8 vCPU, >= 32 GB |
| disk | >= 100 GB |
| network | >= 500 Mbps down, >= 200 Mbps up |

1. List the matching offers (10 cheapest):

   ```bash
   make vast-offers
   ```

2. Rent the cheapest offer. It waits for READY and prints the cold-start time (create to running, running to READY):

   ```bash
   make vast-up
   ```

3. Open the tunnel and keep that terminal open:

   ```bash
   make vast-tunnel
   ```

   Point ThaiDub at `http://localhost:4000/v1` with `LITELLM_API_KEY=<LITELLM_MASTER_KEY>`. Port 8001 (backend,
   no auth) is forwarded too, only for `make bench` and the voices check.
4. Confirm it is the same voice bank as last time. The `sha256` must match the previous rental:

   ```bash
   curl -s localhost:8001/voices-manifest
   ```

Other commands:

```bash
make vast-ssh      # shell on the rented box
```

```bash
make vast-logs     # tail -f /data/start.log (phase fetch, phase load, phase warm-up, cold start total, READY)
```

```bash
make vast-down     # destroys the rental AND its disk, after you type the instance id
```

The rental id is kept in `.vast-instance` (never commit it).

### Web console alternative

```bash
make vast-template
```

This creates a private template (`thaidub-gpu`). Open `https://cloud.vast.ai/?template_id=<hash>` with the
`hash_id` from the output to rent in one click from the browser. SSH launch mode replaces the image entrypoint, so
the template's on-start command is `/app/scripts/vast-onstart.sh`, which starts the server.

## Working on the box with Claude Code

The image ships Claude Code CLI 2.1.292 (pinned, auto-update off), plus git, tmux, vim-tiny and node.

```bash
make vast-ssh
```

```bash
cd /app && claude
```

- Auth comes from `CLAUDE_CODE_OAUTH_TOKEN` (or `ANTHROPIC_API_KEY`). `vast-onstart.sh` passes it to SSH sessions
  through `/etc/environment`, which is root-only.
- The repo's `CLAUDE.md` agent rules and `.claude/settings.json` are in `/app`. The first `claude` run asks you to
  trust the folder and install the ponytail and caveman plugins; after that they turn on every session.
- `/app` is a copy without `.git`. To commit from the box, clone the repo there yourself.

## Agent rules

[`CLAUDE.md`](CLAUDE.md) holds the rules for AI agents working in this repo. ponytail full (smallest complete
change) and caveman full (terse replies) are on by default through `.claude/settings.json`, which enables the
plugins from GitHub `DietrichGebert/ponytail` and `juliusbrussee/caveman`. Their hooks need node.

## RunPod (fallback, not yet tried)

Network volumes (Secure Cloud only) mount at `/workspace`, so set `HF_HOME=/workspace/hf`. Pods use the image's start
command and env vars from the template. Open item: how RunPod's SSH reaches a custom image (sshd setup) still needs checking before the tunnel works there.

### Destroy and recreate

Instance disk is deleted on destroy, so the next rental downloads the weights again (cold start). To skip that, keep
the cache on a provider volume:

- **Vast:** create a volume, mount it at `/workspace`, and set `HF_HOME=/workspace/hf`. A volume costs storage
  while no instance runs. Vast lists volumes per machine, so a recreated instance must be on the same machine to
  reuse it. That last point is inferred from the docs; check it before relying on it.

Rule of thumb: rent briefly and rarely, and let the instance disk re-download; rent daily, and use a volume. Steps:

1. Note the voices `sha256`.
2. `make vast-down`.
3. `make vast-up`, wait for `READY`, and `make vast-tunnel` again.
4. Check the `sha256` matches.

## Check and measure

On the instance (`make vast-ssh`; if the key is not in your shell, pass it):

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
