"""Dynamic batching: asyncio callers submit jobs, ONE worker thread owns the GPU and runs them in groups."""

from __future__ import annotations

import asyncio
import logging
import queue
import threading
import time
from typing import Any, Callable, Sequence

from .stats import BatchStats

log = logging.getLogger("backend.batcher")


class Batcher:
    def __init__(self, run_batch: Callable[[Sequence[Any]], Sequence[Any]], max_size: int, wait_ms: float,
                 stats: BatchStats | None = None) -> None:
        self._run_batch, self._max_size, self._wait_s = run_batch, max(1, max_size), wait_ms / 1000
        self.stats = stats or BatchStats()
        self._queue: queue.Queue = queue.Queue()
        self._thread = threading.Thread(target=self._work, name="gpu-worker", daemon=True)
        self._thread.start()

    def submit(self, job: Any) -> asyncio.Future:
        future = asyncio.get_running_loop().create_future()
        self._queue.put((job, future, future.get_loop(), time.perf_counter()))
        return future

    def close(self) -> None:
        self._queue.put(None)

    def _collect(self, first: tuple) -> list[tuple]:
        """First job plus whatever else arrives within the wait window, up to max_size."""
        items, deadline = [first], time.monotonic() + self._wait_s
        while len(items) < self._max_size:
            try:
                item = self._queue.get(timeout=max(0.0, deadline - time.monotonic()))
            except queue.Empty:
                break
            if item is None:
                self._queue.put(None)  # let the main loop see the stop signal after this batch
                break
            items.append(item)
        return items

    def _work(self) -> None:
        while (first := self._queue.get()) is not None:
            items = self._collect(first)
            began = time.perf_counter()
            queue_wait_ms = [(began - item[3]) * 1000 for item in items]
            try:
                results, error = self._run_batch([item[0] for item in items]), None
            except Exception as exc:  # one failing batch must not kill the worker
                log.exception("gpu batch failed")
                results, error = None, exc
            gpu_ms = (time.perf_counter() - began) * 1000
            if not error:
                self.stats.record(len(items), queue_wait_ms, gpu_ms)
            log.info("gpu batch size=%d gpu_ms=%.0f max_queue_wait_ms=%.0f%s", len(items), gpu_ms, max(queue_wait_ms),
                     " FAILED" if error else "")
            for index, (_, future, loop, _) in enumerate(items):
                loop.call_soon_threadsafe(self._resolve, future, None if error else results[index], error)

    @staticmethod
    def _resolve(future: asyncio.Future, result: Any, error: Exception | None) -> None:
        if future.done():  # caller gave up (cancelled)
            return
        future.set_exception(error) if error else future.set_result(result)
