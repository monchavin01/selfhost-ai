"""One /v1/audio/speech request: split into chunks, batch them on the GPU, join, limit, encode."""

from __future__ import annotations

import asyncio
from typing import Callable

from .audio import limit_peak, cross_fade_join, split_into_chunks, wav_bytes
from .batcher import Batcher
from .errors import ApiError
from .voices import Voice

MAX_SPEED = 1.15  # past this the clip voice stops sounding natural
MAX_INPUT_CHARS = 5000


async def speak(batcher: Batcher, voice: Voice, text: str, seed: int, speed: float,
                split_long_run: Callable[[str, int], list[str]]) -> bytes:
    from .f5_jobs import ChunkJob  # torch-free dataclass

    chunks = split_into_chunks(text, split_long_run)
    if not chunks:
        raise ApiError(400, "input has no speakable text")
    waves = await asyncio.gather(*(batcher.submit(ChunkJob(chunk, voice, seed, speed)) for chunk in chunks))
    return wav_bytes(limit_peak(cross_fade_join(waves)))
