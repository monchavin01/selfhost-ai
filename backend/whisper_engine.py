"""faster-whisper models (CTranslate2, fp16 on cuda) plus the OpenAI-shaped response builder (torch-free)."""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any, Iterable


def build_response(segments: Iterable[Any], info: Any, response_format: str, with_words: bool) -> dict:
    """OpenAI `json` -> {"text"}; `verbose_json` -> text, language, duration, segments (+ top-level words)."""
    segments = list(segments)
    text = "".join(s.text for s in segments).strip()
    if response_format == "json":
        return {"text": text}
    result: dict[str, Any] = {
        "task": "transcribe", "language": info.language, "duration": round(info.duration, 3), "text": text,
        "segments": [{"id": s.id, "seek": s.seek, "start": round(s.start, 3), "end": round(s.end, 3),
                      "text": s.text.strip(), "tokens": list(s.tokens), "temperature": getattr(s, "temperature", 0.0),
                      "avg_logprob": s.avg_logprob, "compression_ratio": s.compression_ratio,
                      "no_speech_prob": s.no_speech_prob} for s in segments]}
    if with_words:
        result["words"] = [{"start": round(w.start, 3), "end": round(w.end, 3), "word": w.word}
                           for s in segments for w in (s.words or [])]
    return result


class WhisperEngines:
    def __init__(self, device: str = "cuda", compute_type: str = "float16") -> None:
        self._device, self._compute_type, self._models = device, compute_type, {}

    def load(self, name: str, path: Path) -> None:
        from faster_whisper import WhisperModel

        self._models[name] = WhisperModel(str(path), device=self._device, compute_type=self._compute_type)

    @property
    def names(self) -> list[str]:
        return sorted(self._models)

    def transcribe(self, model: str, audio: bytes, language: str | None, temperature: float, prompt: str | None,
                   response_format: str, with_words: bool) -> dict:
        """Blocking; run it in a thread. The segment generator is consumed here, where the decoding happens."""
        segments, info = self._models[model].transcribe(
            io.BytesIO(audio), language=language or None, temperature=temperature, initial_prompt=prompt or None,
            word_timestamps=with_words)
        return build_response(list(segments), info, response_format, with_words)
