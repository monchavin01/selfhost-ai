"""models.yaml loader, torch-free so scripts/fetch_models.py can import it too."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Mapping

import yaml

DEFAULT_PATH = "/app/models.yaml"


def load_models(path: str | Path | None = None) -> dict[str, dict[str, Any]]:
    path = Path(path or os.environ.get("MODELS_MANIFEST", DEFAULT_PATH))
    return yaml.safe_load(path.read_text(encoding="utf-8"))["models"]


def is_enabled(entry: Mapping[str, Any], env: Mapping[str, str] = os.environ) -> bool:
    """An entry with `enabled_by: VAR` is only wanted when VAR=1."""
    flag = entry.get("enabled_by")
    return flag is None or env.get(flag, "0") == "1"


def enabled_models(models: Mapping[str, dict[str, Any]], env: Mapping[str, str] = os.environ) -> dict[str, dict[str, Any]]:
    return {name: entry for name, entry in models.items() if is_enabled(entry, env)}


def local_path(entry: Mapping[str, Any]) -> Path:
    """Folder of the already-downloaded pinned snapshot; raises if fetch_models.py has not run."""
    from huggingface_hub import snapshot_download  # lazy: keep import of this module cheap

    return Path(snapshot_download(entry["repo"], revision=entry.get("revision"),
                                  allow_patterns=entry.get("files"), local_files_only=True))
