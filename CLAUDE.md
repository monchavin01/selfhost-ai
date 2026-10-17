# Agent rules — ThaiDub GPU model server

This repo serves ThaiDub's models (F5-TTS-THAI, whisper-th, whisper-zh, optional hunyuan-mt-7b) from ONE rented
GPU (Vast.ai RTX 3090 24 GB; RunPod fallback) behind LiteLLM. Plan, decisions and interfaces:
`docs/plans/thaidub-gpu-server.md` — read it before changing behaviour; update it when a decision changes.

## Session defaults

- Start every session with **ponytail full** (smallest complete change) and **caveman full** (terse replies).
  Both are enabled as plugins in `.claude/settings.json` and turn on by themselves at session start (their hooks
  need `node`). If a plugin is missing, invoke the skill by name (`ponytail`, `caveman`) or follow their rules from
  memory, and tell the owner the plugin did not load.
- Caveman does not apply to text pasted elsewhere as-is: commit messages, PR descriptions, docs, code comments.
- Ask the owner questions in Thai.
- Subagents: tell each to work in caveman full + ponytail full and report briefly; point them at the plan file
  instead of restating it.

## The API contract is fixed

ThaiDub's client (repo `thaidub`, branch `real-4`) sends exactly what the plan's "API contract" and "Interfaces"
sections list. Never change a request or response shape, model name or error code without the owner's say-so.
Errors: 400 bad input, 401 wrong key (LiteLLM), 404 unknown/refused voice, 429 + Retry-After over the rpm limit,
503 not ready, 5xx temporary.

## Where things go

| path | holds |
|---|---|
| `backend/` | FastAPI app; torch/f5/faster_whisper imported lazily so `backend/tests` run without a GPU |
| `scripts/` | start, onstart, fetch, voices, status, bench, vast — bash or stdlib Python |
| `models.yaml` | every model, pinned to a repo commit — no unpinned weights |
| `litellm.yaml` | routes and limits; its header lists LiteLLM behaviours still to verify on the GPU |
| `voices/` | local only: `<voice_id>.neutral.th.wav` + `.txt` (gitignored); only `manifest.sha256` is tracked |

## Engineering practice

- Follow existing patterns and reuse helpers (e.g. `backend/models_manifest.py` is the only models.yaml reader).
- Business-language names (voice, line, chunk, heard check), type hints, small modules.
- Pin every version (images, pip, Claude Code). Comments only for the non-obvious why.
- Tests describe business rules from the user's side (voice refused when its hash mismatches, verbose_json has
  words...). Flag purely presentational code to the owner instead of testing it by default.
- The repo is PUBLIC. Never commit voice clips or transcripts (real people's voices, copyrighted lines), `.env`,
  hostnames/IPs of private machines, or personal paths. Voices reach the box by rsync (`make vast-voices`).
- Secrets only from env (`LITELLM_MASTER_KEY`, `HF_TOKEN`, `CLAUDE_CODE_OAUTH_TOKEN`). Never in git, logs or
  command output; request logs hold model, duration and status only.
- Present real trade-offs to the owner as a choice instead of picking silently.
- Say plainly what was verified and what was not; cite the file, log line or doc behind a claim.

## Where to run things

- The owner's Mac has no NVIDIA GPU and little disk: never pull or build images, install torch/vLLM or load models
  there. Only `make test` (unit tests) and static checks (`bash -n`, `py_compile`) run locally.
- Everything GPU-side is checked on the rented box in one pass (`make vast-up`, then `scripts/bench.py`).
- `make vast-down` destroys the rental (disk included) — always confirm with the owner first.

## Commits

Small, one concern each, conventional prefix (`feat:`, `fix:`, `docs:`, `build:`, `perf:`, `refactor:`,
`chore:`), subject says the outcome, body says why. Never commit `.env` or `.vast-instance`.
