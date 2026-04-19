# Profiles

Each profile activates one main model on the GPU plus (usually) the tiny
Qwen FIM autocomplete model. You pick the profile via `make switch P=<name>`.
Users call it via `model: "<name>"` in their OpenAI-compatible request.

## At a glance

| Name     | Main model              | Params (total / active) | Speed   | Ctx  | SWE-bench* | Reasoning | Agentic | Concurrency |
|----------|-------------------------|-------------------------|---------|------|------------|-----------|---------|-------------|
| `fast`   | GPT-OSS 20B (MXFP4)     | 20B / 3.6B              | 140 t/s | 32K  | ~34%       | toggleable low/med/high | good  | excellent (6) |
| `coder`  | Qwen3-Coder 30B-A3B     | 30B / 3B                | 60 t/s  | 64K  | ~51%       | implicit  | great    | good (3)    |
| `reason` | Nemotron 3 Nano 30B-A3B | 30B / 3B                | 55 t/s  | 128K | ~48%       | toggleable | good   | good (2)    |
| `smart`  | GLM-4.5-Air Q3 (MoE)    | 106B / 12B              | 15 t/s  | 32K  | ~55%       | thinking mode | great | 1 user at a time |

*SWE-bench Verified, community-reported approximate scores. Absolute numbers
vary by scaffold. Relative ordering is the signal.

Plus two non-switchable models always available:

- `coding-fast` — Qwen2.5-Coder 1.5B. ~50ms TTFT. Tab autocomplete in your
  IDE. Runs alongside `fast`, `coder`, `reason`. Not available during `smart`
  (that profile uses every spare byte of VRAM for expert attention).
- `coding-smart` — NVIDIA Nemotron 3 Super 120B via OpenRouter's free
  tier. ~60% SWE-bench. Free. Not private (traffic leaves your network).
  Use for one-off hard problems without disrupting the locally loaded
  profile.

## Pick by task

### Autocomplete in the IDE
→ Use `coding-fast`. Works with any profile except `smart`.

### Ordinary chat, "explain this code", small refactors
→ `fast`. Fastest, handles two humans chatting concurrently.

### Multi-file refactor, tool-use agents, writing tests
→ `coder`. Trained on coding and agentic workflows specifically. 64K
context fits most real projects.

### Long debugging session with hundreds of log lines
→ `reason`. 128K context, toggleable reasoning trace before answer.
Hybrid Mamba-Transformer means long context doesn't blow up KV cache.

### Genuinely hard problem, willing to wait
→ `smart`. Best quality of any local option. 15 tok/s means a 500-token
answer takes ~30 seconds. Pin it with `make lock P=smart` for overnight
batch work.

### Anything that needs frontier reasoning and isn't sensitive
→ `coding-smart` (OpenRouter). 60% SWE-bench, free, no GPU swap.

## Pick by user load

| Scenario                               | Profile    |
|----------------------------------------|------------|
| Two humans in parallel                 | `fast`     |
| One human + an agent running tool loops | `coder`   |
| One human doing a big debug            | `reason`   |
| Queued batch job, one at a time        | `smart`    |

## Concrete recommendations

**Default for most days**: `coder`. It's the best balance of quality,
speed, and agentic capability for coding work.

**When to switch to `fast`**: two people are actively typing/chatting
and you need low latency more than peak quality.

**When to switch to `reason`**: you're going to attach a 50K-line log
file, or set up an agent that will make 20+ tool calls.

**When to switch to `smart`**: genuinely hard architectural question
where 30% better SWE-bench matters more than the 4× latency.

**When to use `coding-smart` instead**: task is hard AND not sensitive.
Beats every local profile on SWE-bench, runs on someone else's GPU,
costs nothing. Make sure OPENROUTER_API_KEY is set in `.env`.

## Trade-off: why not run a bigger model?

With 16GB VRAM you can't run GPT-OSS 120B or Nemotron 3 Super 120B at
interactive speed. Even aggressive MoE offload to RAM needs 64GB+ system
RAM (you have 32GB). If you can add 32GB more RAM, `docs/operations.md`
covers how to add a `super` profile that runs the 120B-class models.
Otherwise the ceiling is `smart` at 15 tok/s.

## Model sources

All GGUF weights come from [Unsloth](https://huggingface.co/unsloth).
GPT-OSS 20B uses OpenAI's official MXFP4 release via vLLM. On first
switch to a profile, the weights download to the shared Hugging Face
cache volume — runs once per model.
