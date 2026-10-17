"""VRAM cost of loading a model. ctranslate2 (whisper) is invisible to torch, so nvidia-smi is the main number."""

from __future__ import annotations

import subprocess
from typing import Callable, TypeVar

T = TypeVar("T")
MIB = 1024 * 1024


def gpu_used_mib() -> int | None:
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=10, check=True).stdout
        return sum(int(line) for line in out.split())
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def _torch_mib() -> tuple[float, float] | None:
    try:
        import torch

        return torch.cuda.memory_allocated() / MIB, torch.cuda.memory_reserved() / MIB
    except Exception:
        return None


def measure_load(name: str, load: Callable[[], T], report: dict[str, dict]) -> T:
    """Run `load()` and store its VRAM delta in report[name]: nvidia_smi, torch_allocated, torch_reserved (MiB)."""
    smi_before, torch_before = gpu_used_mib(), _torch_mib()
    result = load()
    smi_after, torch_after = gpu_used_mib(), _torch_mib()
    entry: dict[str, int | None] = {"nvidia_smi": None if None in (smi_before, smi_after) else smi_after - smi_before}
    if torch_before and torch_after:
        entry["torch_allocated"] = round(torch_after[0] - torch_before[0])
        entry["torch_reserved"] = round(torch_after[1] - torch_before[1])
    report[name] = entry
    return result
