# Architecture

## Two control planes

The single most important design choice: user requests and admin actions
go through different services with different privileges.

```
┌──────────────────────────────────────────────────────────────────┐
│ USER PLANE — LiteLLM :4000                                       │
│                                                                  │
│   • Accepts OpenAI-compatible API calls                          │
│   • Mounts /state read-only  → can SEE active profile            │
│                              → CANNOT change it                  │
│   • No Docker socket         → cannot touch containers            │
│   • Rejects mismatched requests with HTTP 503 + guidance          │
└──────────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────────┐
│ ADMIN PLANE — admin-api :8080 (loopback only)                    │
│                                                                  │
│   • Authenticated by ADMIN_TOKEN (separate from user key)        │
│   • Mounts /state read-write + /var/run/docker.sock              │
│   • Issues `docker compose` commands to switch profiles          │
│   • Polls the new backend until it's ready before returning      │
└──────────────────────────────────────────────────────────────────┘
```

### Why separate

- **Predictability.** Users can't cause 90-second GPU swaps. Latency is
  stable.
- **Blast radius.** If a user API key leaks, the attacker cannot stop or
  swap containers.
- **Fairness.** With two humans on one GPU, auto-switching would cause
  ping-pong. Admin decides; everyone agrees.
- **Auditing.** All state changes go through one small service that's
  easy to log and reason about.

## Profile switching flow

```
1. admin →  POST :8080/switch  {profile: coder}
              │
              ├─ check ADMIN_TOKEN
              ├─ check /state/locked  (reject if set)
              ├─ check /state/switching  (reject if concurrent switch)
              ├─ create /state/switching  (lockfile)
              │
              ├─ docker stop vllm-main vllm-fim
              ├─ docker compose --profile coder up -d
              │
              ├─ poll http://vllm-main:8000/v1/models  (up to 180s)
              │
              ├─ write "coder" → /state/active-profile
              ├─ delete /state/switching
              │
              └─ return 200 OK

2. user →  POST :4000/v1/chat/completions  {model: coder, ...}
              │
              ├─ LiteLLM dispatches to pre-call hook (profile_guard.py)
              ├─ hook reads /state/active-profile  →  "coder"
              ├─ model matches → pass through
              │
              └─ forward to http://vllm-main:8000 → response
```

If the user had asked for `reason` while `coder` is active, the hook
responds:

```json
{
  "error": "profile_not_active",
  "message": "Model 'reason' is not currently available. The active profile is 'coder'. Use model='coder' instead, or ask the admin to switch.",
  "requested": "reason",
  "active": "coder",
  "admin_locked": false
}
```

## VRAM budget

One 16GB GPU, one profile at a time. The shared autocomplete (Qwen FIM
1.5B) runs alongside three of the four profiles.

```
┌──────────────────────────────────────────────────────────────────┐
│ Profile: fast                                                    │
│   GPT-OSS 20B (MXFP4)           13 GB                            │
│   Qwen FIM 1.5B                  2 GB                            │
│   Overhead + KV cache            1 GB                            │
│   ─────────────────────────────────────                          │
│   Total                         16 GB  ✓                         │
└──────────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────────┐
│ Profile: coder / reason                                          │
│   30B-A3B MoE (Q4_K_XL)         11 GB                            │
│   Qwen FIM 1.5B                  2 GB                            │
│   KV cache (fp8)                ~2 GB                            │
│   Overhead                      ~1 GB                            │
│   ─────────────────────────────────────                          │
│   Total                         16 GB  ✓                         │
└──────────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────────┐
│ Profile: smart (GLM-4.5-Air Q3, 106B MoE with CPU offload)       │
│   Attention layers on GPU        8 GB                            │
│   KV cache (q8)                  4 GB                            │
│   Overhead                       2 GB                            │
│   ─────────────────────────────────────                          │
│   GPU total                     14 GB  ✓                         │
│                                                                  │
│   Expert layers in RAM          ~20 GB                           │
│   + mmap-paged from SSD as needed                                │
│                                                                  │
│   Trade-off: ~15 tok/s instead of 60+ because experts go through │
│   the PCIe bus on every token. Good for single slow reasoning.   │
│   No FIM alongside — leave IDE autocomplete off for this profile.│
└──────────────────────────────────────────────────────────────────┘
```

## Storage layout

All models share one Hugging Face cache volume. Download once, every
profile reads the same files.

```
Docker volume: hf-cache
├── GPT-OSS 20B (MXFP4)              13 GB
├── Qwen2.5-Coder 1.5B (FP16)         3 GB
├── Qwen3-Coder 30B-A3B Q4_K_XL      18 GB
├── Nemotron 3 Nano 30B Q4_K_XL      18 GB
└── GLM-4.5-Air Q3_K_XL              48 GB
                                    ─────
                                    100 GB
```

Plus Docker images (~15 GB) and Postgres/Langfuse data (~1 GB).

## Observability

Langfuse runs alongside LiteLLM as an async callback. Every chat
completion that goes through the gateway is traced:

- Full prompt + response
- Latency (TTFT, total, tokens/second)
- Token counts
- Model name, API key used
- User tags (if the client passes them)

Open http://localhost:3000 to browse traces. Useful for:

- Debugging agents that got stuck in a loop
- Finding slow queries that might indicate the wrong profile is active
- Reviewing what a given key has been doing
- Exporting data to compute eval scores

LiteLLM and Langfuse are complementary:

| Concern                   | LiteLLM                    | Langfuse                  |
|---------------------------|----------------------------|---------------------------|
| In the request path?      | Yes                        | No (async callback)       |
| If it dies                | Requests fail              | Requests succeed, no logs |
| UI purpose                | Virtual keys, budgets      | Traces, sessions, eval    |

## Security posture

- User key (`LITELLM_MASTER_KEY`) and admin token (`ADMIN_TOKEN`) are
  separate. Leak one, the other still works.
- Admin API binds to `127.0.0.1:8080` by default. To control from another
  machine, use SSH port-forward or a VPN; don't just expose it.
- Docker socket mount is scoped to `admin-api` only. LiteLLM (the one
  thing users can reach) has no host access.
- All chat traffic and admin control run inside the `coding-stack` Docker
  network. The only host-exposed ports are 4000 (user), 8080 (admin,
  loopback), 3000 (Langfuse UI).
