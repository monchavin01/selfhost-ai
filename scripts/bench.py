#!/usr/bin/env python3
"""Benchmark and contract-check a live ThaiDub GPU server (stdlib only).

Sections (pick with --only, default all):
  speech      F5 speech at 1/4/8 concurrent requests: wall time, req/s, audio-seconds per wall-second,
              p50/p95 latency, speedup vs serial; every reply must start with RIFF. Then dumps <backend>/stats.
  contention  p50 latency of f5 (4 concurrent), whisper-th, whisper-zh verbose_json and hunyuan, each alone
              then all at once; prints the slowdown factor. Hunyuan is skipped if /v1/models does not list it.
  vram        <backend>/ready `vram_mib` per model plus nvidia-smi memory.total; says whether it all fits.
  contract    wrong key -> 401; burst over the f5-tts-th rpm -> a 429 and its Retry-After;
              whisper-zh verbose_json has segments and words; speech is RIFF mono 24 kHz 16-bit.

Needs env LITELLM_MASTER_KEY. Exit code 1 if any contract check fails.
"""
import argparse
import io
import json
import os
import statistics
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
import wave
from concurrent.futures import ThreadPoolExecutor

THAI_LINES = [
    "สวัสดีครับ วันนี้อากาศดีมาก",
    "ขอบคุณที่มาร่วมงานกับเราในวันนี้",
    "กรุณารอสักครู่ ระบบกำลังประมวลผล",
    "ราคาสินค้านี้รวมภาษีมูลค่าเพิ่มแล้ว",
    "เราจะส่งของให้ภายในสามวันทำการ",
    "ยินดีต้อนรับสู่บริการของเรา มีอะไรให้ช่วยไหมครับ",
]
SPEED_LEVELS = (1, 4, 8)
REQUEST_TIMEOUT_S = 300


class Reply:
    def __init__(self, status, body, headers, seconds):
        self.status, self.body, self.headers, self.seconds = status, body, headers, seconds

    def json(self):
        return json.loads(self.body)


def send(req):
    start = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_S) as r:
            status, body, headers = r.status, r.read(), r.headers
    except urllib.error.HTTPError as e:
        status, body, headers = e.code, e.read(), e.headers
    except OSError as e:  # connection refused, timeout
        status, body, headers = 0, str(e).encode(), {}
    return Reply(status, body, headers, time.perf_counter() - start)


class Server:
    def __init__(self, base_url, backend_url, key):
        self.base, self.backend, self.key = base_url.rstrip("/"), backend_url.rstrip("/"), key

    def _auth(self, key=None):
        return {"Authorization": f"Bearer {key or self.key}"}

    def get(self, url):
        return send(urllib.request.Request(url, headers=self._auth()))

    def post_json(self, path, payload, key=None):
        headers = {**self._auth(key), "Content-Type": "application/json"}
        return send(urllib.request.Request(self.base + path, json.dumps(payload).encode(), headers))

    def transcribe(self, model, wav, response_format="json", extra=None):
        fields = {"model": model, "response_format": response_format, **(extra or {})}
        boundary = uuid.uuid4().hex
        parts = []
        for name, value in fields.items():
            for v in value if isinstance(value, list) else [value]:
                key = name + "[]" if isinstance(value, list) else name
                parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{v}\r\n'.encode())
        parts.append((f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="a.wav"\r\n'
                      "Content-Type: audio/wav\r\n\r\n").encode() + wav + f"\r\n--{boundary}--\r\n".encode())
        headers = {**self._auth(), "Content-Type": f"multipart/form-data; boundary={boundary}"}
        return send(urllib.request.Request(self.base + "/audio/transcriptions", b"".join(parts), headers))

    def speak(self, text, voice, model="f5-tts-th"):
        return self.post_json("/audio/speech", {"model": model, "input": text, "voice": voice,
                                                "response_format": "wav"})


def wav_seconds(body):
    try:
        with wave.open(io.BytesIO(body)) as w:
            return w.getnframes() / w.getframerate()
    except (wave.Error, EOFError):
        return 0.0


def percentile(values, p):
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, round(p / 100 * (len(ordered) - 1)))]


def run_concurrent(jobs, workers):
    """jobs: list of zero-arg callables returning Reply. Returns (replies, wall_seconds)."""
    start = time.perf_counter()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        replies = list(pool.map(lambda j: j(), jobs))
    return replies, time.perf_counter() - start


def print_table(headers, rows):
    cells = [headers] + [[str(c) for c in r] for r in rows]
    widths = [max(len(r[i]) for r in cells) for i in range(len(headers))]
    for n, row in enumerate(cells):
        print("  ".join(c.ljust(w) for c, w in zip(row, widths)))
        if n == 0:
            print("  ".join("-" * w for w in widths))
    print()


def speech_jobs(server, voices, count):
    return [lambda i=i: server.speak(THAI_LINES[i % len(THAI_LINES)], voices[i % len(voices)])
            for i in range(count)]


def list_voices(server):
    reply = server.get(server.backend + "/voices-manifest")
    if reply.status != 200:
        sys.exit(f"voices-manifest failed: HTTP {reply.status} {reply.body[:200]!r}")
    voices = reply.json()["voices"]
    if not voices:
        sys.exit("voices-manifest lists no voices")
    return voices


# ---------- 1. speech throughput ----------
def bench_speech(server, voices, requests_per_level):
    rows, report, serial_rps = [], {}, None
    for level in SPEED_LEVELS:
        count = max(requests_per_level, level)
        replies, wall = run_concurrent(speech_jobs(server, voices, count), level)
        bad = [r for r in replies if r.status != 200 or not r.body.startswith(b"RIFF")]
        latencies = [r.seconds for r in replies]
        audio = sum(wav_seconds(r.body) for r in replies if r not in bad)
        rps = count / wall
        serial_rps = serial_rps or rps
        report[level] = {"requests": count, "failed_or_not_riff": len(bad), "wall_s": wall, "rps": rps,
                         "audio_s_per_wall_s": audio / wall, "p50_s": statistics.median(latencies),
                         "p95_s": percentile(latencies, 95), "speedup_vs_serial": rps / serial_rps}
        rows.append([level, count, len(bad), f"{wall:.1f}", f"{rps:.2f}", f"{audio / wall:.2f}",
                     f"{report[level]['p50_s']:.2f}", f"{report[level]['p95_s']:.2f}", f"{rps / serial_rps:.2f}x"])
    print("== F5 speech throughput ==")
    print_table(["conc", "reqs", "bad", "wall_s", "req/s", "audio_s/wall_s", "p50_s", "p95_s", "speedup"], rows)
    return report


def dump_stats(server):
    reply = server.get(server.backend + "/stats")
    print("== backend /stats (F5 batch histogram) ==")
    stats = reply.json() if reply.status == 200 else {"error": f"HTTP {reply.status}"}
    print(json.dumps(stats, indent=2, ensure_ascii=False), "\n")
    return stats


# ---------- 2. contention ----------
def contention_workloads(server, voices, wav):
    """name -> (list of jobs, workers). Each job returns a Reply."""
    workloads = {
        "f5-tts-th x4": (speech_jobs(server, voices, 8), 4),
        "whisper-th": ([lambda: server.transcribe("whisper-th", wav, extra={"language": "th"})] * 4, 2),
        "whisper-zh verbose": ([lambda: server.transcribe(
            "whisper-zh", wav, "verbose_json", {"timestamp_granularities": ["word", "segment"]})] * 4, 2),
    }
    models = server.get(server.base + "/models")
    if models.status == 200 and "hunyuan-mt-7b" in {m["id"] for m in models.json().get("data", [])}:
        chat = {"model": "hunyuan-mt-7b", "temperature": 0.7, "max_tokens": 64,
                "messages": [{"role": "user", "content": "Translate to English: สวัสดีครับ ขอบคุณมาก"}]}
        workloads["hunyuan chat"] = ([lambda: server.post_json("/chat/completions", chat)] * 4, 2)
    else:
        print("(hunyuan-mt-7b not listed in /v1/models: skipped)")
    return workloads


def bench_contention(server, voices):
    print("== contention: p50 latency alone vs all at once ==")
    seed = server.speak(THAI_LINES[0], voices[0])
    if not seed.body.startswith(b"RIFF"):
        sys.exit(f"cannot generate heard-check wav: HTTP {seed.status}")
    workloads = contention_workloads(server, voices, seed.body)

    alone = {}
    for name, (jobs, workers) in workloads.items():
        replies, _ = run_concurrent(jobs, workers)
        alone[name] = statistics.median(r.seconds for r in replies)

    everything = [(name, job) for name, (jobs, _) in workloads.items() for job in jobs]
    replies, _ = run_concurrent([j for _, j in everything], len(everything))
    mixed = {}
    for (name, _), reply in zip(everything, replies):
        mixed.setdefault(name, []).append(reply.seconds)

    report, rows = {}, []
    for name in workloads:
        m = statistics.median(mixed[name])
        report[name] = {"alone_p50_s": alone[name], "mixed_p50_s": m, "slowdown": m / alone[name]}
        rows.append([name, f"{alone[name]:.2f}", f"{m:.2f}", f"{m / alone[name]:.2f}x"])
    print_table(["workload", "alone_p50_s", "mixed_p50_s", "slowdown"], rows)
    return report


# ---------- 3. VRAM ----------
def gpu_total_mib():
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, check=True).stdout
        return int(out.splitlines()[0].strip())
    except (OSError, subprocess.CalledProcessError, ValueError, IndexError):
        return None


def check_vram(server):
    print("== VRAM ==")
    reply = server.get(server.backend + "/ready")
    per_model = reply.json().get("vram_mib", {}) if reply.status == 200 else {}
    for name, mib in per_model.items():
        print(f"  {name}: {mib} MiB")
    used, total = sum(per_model.values()), gpu_total_mib()
    print(f"  total used by models: {used} MiB")
    if total is None:
        print("  nvidia-smi unavailable: cannot say whether it fits (run this on the GPU host)\n")
        return {"per_model_mib": per_model, "used_mib": used, "card_mib": None, "fits": None}
    fits = used <= total
    print(f"  card memory.total: {total} MiB -> {'FITS' if fits else 'DOES NOT FIT'} "
          f"({total - used} MiB headroom)\n")
    return {"per_model_mib": per_model, "used_mib": used, "card_mib": total, "fits": fits}


# ---------- 5. contract ----------
def check_speech_format(body):
    with wave.open(io.BytesIO(body)) as w:
        return w.getnchannels() == 1 and w.getframerate() == 24000 and w.getsampwidth() == 2


def check_contract(server, voices, rpm_burst):
    print("== contract checks ==")
    results = {}

    results["wrong key gives 401"] = server.post_json(
        "/audio/speech", {"model": "f5-tts-th", "input": "x", "voice": voices[0]}, key="wrong-key").status == 401

    speech = server.speak(THAI_LINES[0], voices[0])
    try:
        fmt_ok = speech.body.startswith(b"RIFF") and check_speech_format(speech.body)
    except (wave.Error, EOFError):
        fmt_ok = False
    results["speech is RIFF mono 24kHz 16-bit"] = fmt_ok

    verbose = server.transcribe("whisper-zh", speech.body, "verbose_json",
                                {"timestamp_granularities": ["word", "segment"]})
    data = verbose.json() if verbose.status == 200 else {}
    results["whisper-zh verbose_json has segments and words"] = bool(data.get("segments") and data.get("words"))

    # tiny input keeps the burst cheap; with rpm exceeded LiteLLM should answer 429 before the GPU runs
    burst, _ = run_concurrent([lambda: server.speak("ทดสอบ", voices[0]) for _ in range(rpm_burst)], 32)
    limited = [r for r in burst if r.status == 429]
    retry_after = limited[0].headers.get("Retry-After") if limited else None
    results[f"burst of {rpm_burst} gives >=1 429"] = bool(limited)
    print(f"  429 count: {len(limited)}/{rpm_burst}, Retry-After header: {retry_after!r}")

    for name, ok in results.items():
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
    print()
    return results


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("base_url", nargs="?", default="http://localhost:4000/v1", help="LiteLLM /v1 URL")
    ap.add_argument("--backend-url", default="http://127.0.0.1:8001", help="backend (not under /v1)")
    ap.add_argument("--only", choices=["speech", "contention", "vram", "contract"], help="run one section")
    ap.add_argument("--rpm-burst", type=int, default=180, help="requests in the 429 burst (f5-tts-th rpm)")
    ap.add_argument("--requests", type=int, default=16, help="speech requests per concurrency level")
    ap.add_argument("--json", metavar="FILE", help="also write the full report as JSON")
    args = ap.parse_args()

    key = os.environ.get("LITELLM_MASTER_KEY")
    if not key:
        sys.exit("set LITELLM_MASTER_KEY")
    server = Server(args.base_url, args.backend_url, key)
    voices = list_voices(server)
    wanted = lambda s: args.only in (None, s)

    report = {}
    if wanted("speech"):
        report["speech"] = bench_speech(server, voices, args.requests)
        report["stats"] = dump_stats(server)
    if wanted("contention"):
        report["contention"] = bench_contention(server, voices)
    if wanted("vram"):
        report["vram"] = check_vram(server)
    if wanted("contract"):
        report["contract"] = check_contract(server, voices, args.rpm_burst)

    if args.json:
        with open(args.json, "w") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)
    if not all(report.get("contract", {}).values()):
        sys.exit(1)


if __name__ == "__main__":
    main()
