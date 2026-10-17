from __future__ import annotations

from dataclasses import dataclass

from .voices import Voice


@dataclass(frozen=True)
class ChunkJob:
    """One chunk of Thai text to voice; every chunk of a request carries the request's seed."""
    text: str
    voice: Voice
    seed: int
    speed: float
