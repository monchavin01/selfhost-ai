#!/usr/bin/env python3
"""Download enabled models.yaml entries into $HF_HOME at their pinned revision.

  fetch_models.py                 download all enabled entries
  fetch_models.py --get NAME KEY  print one manifest field (repo / revision), used by start.sh
Same models.yaml format as backend/models_manifest.py; parsed directly here so the script has no backend import.
"""
import os
import sys
import time
from pathlib import Path

import yaml
from huggingface_hub import snapshot_download

MANIFEST = Path(os.environ.get("MODELS_YAML", Path(__file__).resolve().parent.parent / "models.yaml"))


def enabled(spec: dict) -> bool:
    flag = spec.get("enabled_by")
    return flag is None or os.environ.get(flag, "0") == "1"


def fetch(name: str, spec: dict) -> bool:
    kwargs = dict(repo_id=spec["repo"], revision=spec["revision"], allow_patterns=spec.get("files"))
    start = time.monotonic()
    try:
        snapshot_download(**kwargs, local_files_only=True)
        print(f"{name}: cached ({time.monotonic() - start:.1f}s)", flush=True)
        return True
    except Exception:  # not (fully) in cache -> download
        pass
    print(f"{name}: downloading {spec['repo']}@{spec['revision'][:8]}", flush=True)
    try:
        snapshot_download(**kwargs, token=os.environ.get("HF_TOKEN") or None)
    except Exception as exc:
        print(f"{name}: FAILED {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        return False
    print(f"{name}: done in {time.monotonic() - start:.1f}s", flush=True)
    return True


def main() -> int:
    models = yaml.safe_load(MANIFEST.read_text())["models"]
    if len(sys.argv) == 4 and sys.argv[1] == "--get":
        print(models[sys.argv[2]][sys.argv[3]])
        return 0
    results = [fetch(n, s) for n, s in models.items() if enabled(s)]
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
