"""Environment contract (see docs/plans/thaidub-gpu-server.md, Interfaces)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    voices_dir: Path
    log_dir: Path
    models_manifest: Path
    batch_max: int
    batch_wait_ms: int
    default_seed: int
    default_voice: str

    @classmethod
    def from_env(cls, env: dict[str, str] | os._Environ = os.environ) -> "Settings":
        return cls(
            voices_dir=Path(env.get("VOICES_DIR", "/app/voices")),
            log_dir=Path(env.get("LOG_DIR", "/data/logs")),
            models_manifest=Path(env.get("MODELS_MANIFEST", "/app/models.yaml")),
            batch_max=int(env.get("F5_BATCH_MAX", "8")),
            batch_wait_ms=int(env.get("F5_BATCH_WAIT_MS", "15")),
            default_seed=int(env.get("F5_DEFAULT_SEED", "0")),
            default_voice=env.get("DEFAULT_VOICE", "m_young_01"),
        )
