# Profiles

Each profile activates one main model on the GPU plus (usually) the tiny
Qwen FIM autocomplete model. You pick the profile via `make switch P=<name>`.
Users call it via `model: "<name>"` in their OpenAI-compatible request.

## At a glance

| Name     | Main model              | Engine     | Params (total / active) | Speed   | Ctx  | SWE-bench* | Reasoning | Agentic | Concurrency |
|----------|-------------------------|------------|-------------------------|---------|------|------------|-----------|---------|-------------|
| `fast`   | GPT-OSS 20B (MXFP4)     | vLLM       | 20B / 3.6B              | 140 t/s | 32K  | ~34%       | toggleable low/med/high | good  | excellent (6) |
| `coder`  | Qwen3-Coder 30B-A3B     | llama.cpp  | 30B / 3B                | 60 t/s  | 64K  | ~51%       | implicit  | great    | good (3)    |
| `reason` | Nemotron 3 Nano 30B-A3B | llama.cpp  | 30B / 3B                | 55 t/s  | 128K | ~48%       | toggleable | good   | good (2)    |
| `smart`  | GLM-4.5-Air Q3 (MoE)    | llama.cpp  | 106B / 12B              | 15 t/s  | 32K  | ~55%       | thinking mode | great | 1 user at a time |
| `qwen3`  | Qwen3-32B Q3_K_XL       | llama.cpp  | 32B / 32B               | 25 t/s  | 32K  | ~52%       | implicit  | great    | 1 user at a time |

*SWE-bench Verified, community-reported approximate scores. Absolute numbers
vary by scaffold. Relative ordering is the signal.

Plus two non-switchable models always available:

- `coding-fast` — Qwen2.5-Coder 1.5B (vLLM). ~50ms TTFT. Tab autocomplete in your
  IDE. Runs alongside `fast`, `coder`, `reason`. Not available during `smart`
  (that profile uses every spare byte of VRAM for expert attention).
- `coding-smart` — NVIDIA Nemotron 3 Super 120B via OpenRouter's free
  tier. ~60% SWE-bench. Free. Not private (traffic leaves your network).
  Use for one-off hard problems without disrupting the locally loaded
  profile.

## Inference engines

| Profile  | Engine    | Why this engine?                                              |
|----------|-----------|---------------------------------------------------------------|
| `fast`   | **vLLM**  | PagedAttention + continuous batching. Best throughput for dense models. GPT-OSS 20B uses MXFP4 which vLLM supports natively. |
| `coder`  | **llama.cpp** | MoE models (Qwen3-Coder 30B-A3B) run efficiently with expert offloading. GGUF quantization from Unsloth reduces VRAM. Flash Attention + Q8 KV cache. |
| `reason` | **llama.cpp** | Same as coder. Mamba-Transformer hybrid benefits from llama.cpp's optimized kernels. 128K context fits thanks to Q8 KV cache. |
| `smart`  | **llama.cpp** | 106B MoE needs partial CPU offload (`--n-cpu-moe 36`). llama.cpp handles CPU+GPU split transparently. |
| `qwen3`  | **llama.cpp** | Qwen3-32B dense at Q3_K_XL. Fits on 24GB+ GPUs. On 16GB, reduce `--n-gpu-layers` to 60 for partial offload. |
| `coding-fast` | **vLLM** | Small 1.5B model. vLLM's prefix caching gives near-instant FIM completions. |

**vLLM vs llama.cpp**: vLLM wins on throughput for dense models. llama.cpp wins on quantization flexibility, MoE support, and CPU offloading. We use the right tool for each model.

## Qwen3 model variants

The user asked about "Qwen3.6 35B-A2B". **This exact model does not exist.** The Qwen3 family includes:

- **Qwen3-30B-A3B** (MoE, 30B total / 3B active) — used in the `coder` profile as *Qwen3-Coder-30B-A3B-Instruct*, optimized for code.
- **Qwen3-32B** (dense, 32B active) — available as the `qwen3` profile. Requires ~16GB at Q3_K_XL plus KV cache, so **24GB+ VRAM is recommended**.

There is no "35B-A2B" variant. If you want the latest Qwen3 for coding, use `make coder`. If you have 24GB+ VRAM and want the dense Qwen3-32B, use `make qwen3`.

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
| One user, max quality Qwen3 dense      | `qwen3`    |

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
