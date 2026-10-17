"""Route behaviour with fake engines; needs httpx (and python-multipart for transcription)."""
import hashlib

import numpy as np
import pytest
import soundfile as sf

pytest.importorskip("httpx")
from fastapi.testclient import TestClient  # noqa: E402

from backend.app import Engines, create_app  # noqa: E402
from backend.batcher import Batcher  # noqa: E402
from backend.settings import Settings  # noqa: E402


@pytest.fixture
def client(tmp_path):
    voices = tmp_path / "voices"
    voices.mkdir()
    sf.write(voices / "v.neutral.th.wav", np.zeros(24000, "float32"), 24000)
    (voices / "v.neutral.th.txt").write_text("สวัสดี", encoding="utf-8")
    (voices / "manifest.sha256").write_text("".join(
        f"{hashlib.sha256((voices / n).read_bytes()).hexdigest()}  {n}\n" for n in ("v.neutral.th.wav", "v.neutral.th.txt")))
    settings = Settings(voices, tmp_path / "logs", tmp_path / "m.yaml", 4, 5, 0, "v")
    fake = Engines(Batcher(lambda jobs: [np.full(2400, 0.5, np.float32) for _ in jobs], 4, 5),
                   lambda p, n: [p], lambda *a: {"text": "x"}, ["f5-tts-th", "whisper-th"], ["whisper-th"],
                   {"f5-tts-th": {"nvidia_smi": 100}})
    with TestClient(create_app(settings, lambda _: fake)) as c:
        for _ in range(100):
            if c.get("/ready").status_code == 200:
                break
        yield c, tmp_path


def speak(c, **extra):
    return c.post("/v1/audio/speech", json={"model": "f5-tts-th", "input": "สวัสดี", "voice": "default", **extra})


def test_speech_returns_wav_and_default_voice_maps(client):
    c, tmp = client
    r = speak(c)
    assert r.status_code == 200 and r.content[:4] == b"RIFF"
    assert (tmp / "logs" / "requests.log").read_text().split()[1] == "f5-tts-th"


def test_bad_requests_get_400_404(client):
    c, _ = client
    assert speak(c, speed=1.2).status_code == 400
    assert speak(c, voice="nobody").status_code == 404
    assert speak(c, voice="../x").status_code == 400
    assert c.post("/v1/audio/speech", json={"voice": "v"}).status_code == 400
    assert c.get("/voices-manifest").json()["voices"] == ["v"]


def test_ready_reports_vram_and_stats_report_batches(client):
    c, _ = client
    assert c.get("/ready").json()["vram_mib"] == {"f5-tts-th": 100}
    speak(c)
    stats = c.get("/stats").json()
    assert stats["batches"] == 1 and stats["batch_size_histogram"] == {"1": 1}
