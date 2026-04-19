# Operations

Day-to-day workflows, common fixes, and knobs you might want to turn.

## Typical day

**Morning**

```bash
make up               # core services (idempotent — safe if already up)
make coder            # pick the profile for today's work
make lock P=coder R='pair programming all day'
```

Locking prevents anyone — including yourself on autopilot — from
accidentally swapping mid-session.

**Midday: someone hits a truly hard problem**

Two options:

1. Have them use `coding-smart` (OpenRouter free frontier). No switch,
   no disruption.
2. Unlock briefly, switch to `smart`, run the query, switch back:
   ```bash
   make unlock
   make smart
   # run the hard query
   make coder
   make lock P=coder R='back to pair programming'
   ```

**End of day**

```bash
make unlock
make off          # stop models, keep core services running
# or:
make down         # stop absolutely everything
```

## First-time setup

```bash
make init               # create .env
$EDITOR .env            # set tokens and passwords
make check              # validate
make build              # build the admin-api container
make up                 # start core services
make warmup             # (optional) download all models overnight
make fast               # activate a profile to start using
make test               # smoke test
```

`make warmup` loops through every profile, triggering downloads. Expect
~100GB of traffic. Once done, future switches between profiles take
30-60 seconds (VRAM load only, no downloads).

## Smoke tests

```bash
make ping               # HTTP health of every service
make status             # active profile, locks, running containers
make test               # actual chat completion against the active profile
```

## Logs

```bash
make logs                  # all services
make logs-litellm          # just the gateway
make logs-admin-api        # just the admin service
make logs-vllm-main        # the currently-loaded model backend
make logs-langfuse         # observability UI
```

For per-request detail, open Langfuse at http://localhost:3000 and browse
traces. Every chat completion is logged there with full context.

## Common situations

### A switch got stuck

Symptoms: `make status` shows `"switching_in_progress": true` for many
minutes, but `docker compose ps` shows no model container.

```bash
make reset       # clears lockfiles, stops model containers, sets profile=off
make fast        # try again
```

### CUDA out of memory during a switch

Rare but possible if VRAM didn't release fast enough.

```bash
make off                    # stop model containers
nvidia-smi                  # should show ~0 MiB used
make <profile>              # try again
```

If `nvidia-smi` still shows memory in use, a process is stuck. Find and
kill it, or reboot.

### User reports HTTP 503 "profile_not_active"

This is working as designed. Either:

1. Tell them which profile is active (`make status`) so they request
   the right model name.
2. Switch to the profile they need: `make switch P=<whatever>`.
3. If this happens a lot, lock the intended profile explicitly so
   expectations are clear.

### Langfuse not receiving traces

Check `make logs-litellm` for callback errors. Most common cause: you
didn't create a Langfuse project + keys yet. Open http://localhost:3000,
create an account, create a project, and export `LANGFUSE_PUBLIC_KEY` /
`LANGFUSE_SECRET_KEY` into `.env`, then `make restart`.

### Model download is very slow

Hugging Face throttles unauthenticated downloads. Confirm `HF_TOKEN` is
set correctly — `make check` validates this. If it's set and still slow,
it's just your internet — downloads resume on retry, so letting it run
overnight is fine.

### A user key got leaked

Rotate only the user key; admin control is unaffected.

```bash
# generate a new key
NEW=$(openssl rand -hex 32)
sed -i "s/^LITELLM_MASTER_KEY=.*/LITELLM_MASTER_KEY=sk-$NEW/" .env
make restart
# notify your users
```

### Admin token got leaked

Rotate `ADMIN_TOKEN` and restart just the admin API.

```bash
NEW=$(openssl rand -hex 32)
sed -i "s/^ADMIN_TOKEN=.*/ADMIN_TOKEN=$NEW/" .env
docker compose up -d --force-recreate admin-api
```

## Configuration tweaks

### Change the port LiteLLM listens on

Edit `docker-compose.yml`, change the `ports:` mapping under `litellm`.
Restart: `make restart`.

### Increase max context per profile

Edit the profile's `command:` in `docker-compose.yml`. For vLLM profiles
that's `--max-model-len`, for llama.cpp profiles it's `--ctx-size`.
Restart the profile: `make off && make <profile>`.

Heads up: larger context = more VRAM for KV cache, which can push the
model into OOM on first heavy query. Profile the actual memory use with
`nvidia-smi` before committing.

### Add a new profile

1. Add a new service block in `docker-compose.yml` with
   `container_name: vllm-main` and a new `profiles: [myprofile]` tag.
2. Add `myprofile` to `VALID_PROFILES` in `admin-api/app.py`.
3. Add a model_list entry in `litellm-config.yaml` for `myprofile`.
4. Add `myprofile` to `SWITCHABLE_MODELS` in `hooks/profile_guard.py`.
5. Rebuild: `make build && make restart`.
6. Optionally add a shortcut in the `Makefile` (see `fast`, `coder`, etc.)

### Add a 64GB-RAM `super` profile for 120B models

If you upgrade RAM to 64GB+, you can run GPT-OSS 120B or Nemotron 3
Super 120B with llama.cpp's `--n-cpu-moe` expert offload.

Add to `docker-compose.yml`:

```yaml
  llama-super:
    image: ghcr.io/ggml-org/llama.cpp:server-cuda
    container_name: vllm-main
    profiles: ["super"]
    # ... (same boilerplate as llama-smart)
    command: >
      -hf unsloth/gpt-oss-120b-GGUF:MXFP4
      --host 0.0.0.0 --port 8000 --api-key dummy
      --ctx-size 32768
      --n-gpu-layers 99
      --n-cpu-moe 28
      --flash-attn
      --cache-type-k q8_0 --cache-type-v q8_0
      --alias main-model
      --jinja
      --parallel 1
```

Then register it through the five add-new-profile steps above. Expected
throughput: 12-20 tok/s, depending on RAM speed.

## Monitoring

Watch during a busy session:

```bash
watch -n 2 nvidia-smi     # VRAM + utilization
docker stats              # per-container CPU/RAM
```

LiteLLM exposes Prometheus metrics at `http://localhost:4000/metrics`.
Point your own Grafana / Prometheus if you want dashboards; it's not
bundled here.

## Backup

The only data worth keeping long-term is the Postgres volume (contains
Langfuse traces and LiteLLM budget/key state). Back up with:

```bash
docker compose exec postgres pg_dumpall -U litellm > backup-$(date +%F).sql
```

Restore on a fresh install:

```bash
cat backup-2026-04-19.sql | docker compose exec -T postgres psql -U litellm
```

Model weights (`hf-cache` volume) are reproducible — no need to back up.

## Upgrading

```bash
git pull
make pull                 # pull new Docker images
make build                # rebuild admin-api if it changed
make restart              # restart core
# reactivate whatever profile was in use
```

Check release notes on each service before major version bumps:
vLLM, llama.cpp, LiteLLM, Langfuse. Breaking changes happen.
