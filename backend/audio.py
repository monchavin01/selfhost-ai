"""Torch-free audio helpers: chunking, cross-fade, peak limit, WAV bytes."""

from __future__ import annotations

import io
import re
from typing import Callable, Sequence

import numpy as np

SAMPLE_RATE = 24000
MAX_CHUNK_CHARS = 100  # the library voices a longer line in chunks of at most this many characters
CROSS_FADE_S = 0.15  # ... and joins them with this cross-fade
PEAK = 10 ** (-1 / 20)  # the raw output clips up to +3.9 dBFS; quiet lines are not made louder


def fixed_width_split(piece: str, max_chars: int) -> list[str]:
    return [piece[i:i + max_chars] for i in range(0, len(piece), max_chars)]


def split_into_chunks(text: str, split_long_run: Callable[[str, int], list[str]] = fixed_width_split,
                      max_chars: int = MAX_CHUNK_CHARS) -> list[str]:
    """Pieces of at most max_chars cut at spaces (Thai writes a space between phrases); a space-less run goes
    to split_long_run (the F5 library's word chunker in production)."""
    text = re.sub("…+", " ", text)  # the normaliser drops the mark anyway; a space keeps the chunking
    chunks: list[str] = []
    for piece in text.split():
        for part in split_long_run(piece, max_chars) if len(piece) > max_chars else [piece]:
            if chunks and len(chunks[-1]) + 1 + len(part) <= max_chars:
                chunks[-1] += " " + part
            else:
                chunks.append(part)
    return chunks


def cross_fade_join(waves: Sequence[np.ndarray], rate: int = SAMPLE_RATE) -> np.ndarray:
    joined = np.asarray(waves[0], dtype=np.float32)
    for wave in waves[1:]:
        wave = np.asarray(wave, dtype=np.float32)
        fade = min(int(CROSS_FADE_S * rate), len(joined), len(wave))
        ramp = np.linspace(1, 0, fade, dtype=np.float32)
        joined = np.concatenate([joined[:len(joined) - fade],
                                 joined[len(joined) - fade:] * ramp + wave[:fade] * ramp[::-1], wave[fade:]])
    return joined


def limit_peak(audio: np.ndarray) -> np.ndarray:
    peak = float(np.abs(audio).max()) if len(audio) else 0.0
    return audio * min(1.0, PEAK / peak) if peak > 0 else audio


def wav_bytes(audio: np.ndarray, rate: int = SAMPLE_RATE) -> bytes:
    import soundfile as sf

    buffer = io.BytesIO()
    sf.write(buffer, np.clip(audio, -1.0, 1.0), rate, format="WAV", subtype="PCM_16")
    return buffer.getvalue()
