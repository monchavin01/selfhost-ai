"""F5 batching numbers for GET /stats: batch-size histogram, queue wait per job, GPU time per batch."""

from __future__ import annotations

import threading
from collections import Counter, deque


def _percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return round(ordered[min(len(ordered) - 1, int(fraction * len(ordered)))], 1) if ordered else 0.0


def _summary(values: deque) -> dict:
    return {"p50": _percentile(list(values), 0.50), "p95": _percentile(list(values), 0.95)}


class BatchStats:
    def __init__(self, window: int = 2000) -> None:
        self._lock = threading.Lock()
        self._sizes: Counter[int] = Counter()
        self._queue_wait_ms: deque[float] = deque(maxlen=window)  # per job: submit -> its batch starts
        self._gpu_ms: deque[float] = deque(maxlen=window)  # per batch
        self._jobs = 0

    def record(self, size: int, queue_wait_ms: list[float], gpu_ms: float) -> None:
        with self._lock:
            self._sizes[size] += 1
            self._jobs += len(queue_wait_ms)
            self._queue_wait_ms.extend(queue_wait_ms)
            self._gpu_ms.append(gpu_ms)

    def snapshot(self) -> dict:
        with self._lock:
            return {"batches": sum(self._sizes.values()), "jobs": self._jobs,
                    "batch_size_histogram": {str(k): v for k, v in sorted(self._sizes.items())},
                    "queue_wait_ms": _summary(self._queue_wait_ms), "gpu_ms_per_batch": _summary(self._gpu_ms)}
