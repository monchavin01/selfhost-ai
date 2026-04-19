# Coding Stack

Self-hosted coding assistant for a team of 2 on one 16GB GPU. Multiple
open-weight models ready to swap in, all behind one OpenAI-compatible API.
Only the admin can change which model is running; users get a clean
`HTTP 503` with a hint if they ask for one that isn't loaded.

```
                       ┌─────────────────┐
   users, agents ─────►│ LiteLLM  :4000  │──► whichever model is loaded
                       └─────────────────┘
                                │
                                │ (read-only)
                                ▼
                         state/active-profile
                                ▲
                                │ (read-write + docker)
                       ┌─────────────────┐
   admin (you)   ─────►│ admin-api :8080 │──► stops / starts containers
                       └─────────────────┘
```

## Quickstart

```bash
make init          # create .env — then edit it (tokens, passwords)
make check         # validate .env, Docker, GPU
make build         # build admin-api image
make up            # start core services
make fast          # activate the GPT-OSS 20B profile (downloads ~13GB first time)
make test          # smoke test
```

Point any OpenAI-compatible client at `http://localhost:4000/v1` with your
`LITELLM_MASTER_KEY`. Models: `fast`, `coder`, `reason`, `smart`,
`coding-fast` (autocomplete), `coding-smart` (free OpenRouter frontier).

## The five profiles

| `make <cmd>` | Model                    | Speed   | Ctx  | Best for              |
|--------------|--------------------------|---------|------|-----------------------|
| `make fast`  | GPT-OSS 20B              | 140 t/s | 32K  | daily chat, concurrent |
| `make coder` | Qwen3-Coder 30B-A3B      | 60 t/s  | 64K  | agentic coding        |
| `make reason`| Nemotron 3 Nano 30B-A3B  | 55 t/s  | 128K | reasoning, long ctx   |
| `make smart` | GLM-4.5-Air Q3           | 15 t/s  | 32K  | hard problems (slow)  |
| `make off`   | —                        | —       | —    | stop all models       |

First switch to a profile takes 30-90s (model load). After that, instant.

## Common tasks

```bash
make                         # help (also: make help)
make status                  # what's running right now
make switch P=coder          # change profile
make lock P=coder R='team session'
make unlock
make logs                    # tail everything
make logs-admin-api          # tail one service
make test                    # smoke-test the active profile
make down                    # stop everything
```

## Docs

- [`docs/architecture.md`](docs/architecture.md) — why two planes, VRAM budget, storage
- [`docs/profiles.md`](docs/profiles.md) — what each profile is good at, benchmarks, trade-offs
- [`docs/operations.md`](docs/operations.md) — day-to-day workflows, troubleshooting

## Requirements

- Linux with NVIDIA driver + `nvidia-container-toolkit`
- Docker 24+ with `compose` plugin
- One GPU with ≥16GB VRAM (tested on RTX 4070 Ti Super)
- 32GB+ RAM, 150GB+ free SSD
- Hugging Face account (for `HF_TOKEN`)

## Repository layout

```
.
├── Makefile              ← run `make` to see everything
├── docker-compose.yml    ← all services
├── litellm-config.yaml   ← user-facing API config
├── admin-api/            ← FastAPI service that owns Docker socket
│   ├── Dockerfile
│   └── app.py
├── hooks/                ← LiteLLM pre-call hook (read-only guard)
│   └── profile_guard.py
├── scripts/admin         ← CLI wrapper around admin-api HTTP
├── state/                ← shared state (active-profile, locks)
└── docs/
```

## License

MIT. See [LICENSE](LICENSE).
