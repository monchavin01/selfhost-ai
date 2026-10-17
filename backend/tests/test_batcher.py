import asyncio
import threading

from backend.batcher import Batcher


def _run(coro):
    return asyncio.run(coro)


def test_jobs_arriving_together_run_in_one_batch_up_to_the_maximum():
    sizes = []

    def fake_engine(jobs):
        sizes.append(len(jobs))
        return [job * 10 for job in jobs]

    async def go():
        batcher = Batcher(fake_engine, max_size=4, wait_ms=200)
        results = await asyncio.gather(*(batcher.submit(i) for i in range(6)))
        batcher.close()
        return results

    assert _run(go()) == [0, 10, 20, 30, 40, 50]  # each caller gets its own result
    assert sizes[0] == 4 and sum(sizes) == 6


def test_lone_job_is_not_held_longer_than_the_wait_window():
    async def go():
        batcher = Batcher(lambda jobs: list(jobs), max_size=4, wait_ms=5)
        result = await asyncio.wait_for(batcher.submit("x"), timeout=2)
        batcher.close()
        return result

    assert _run(go()) == "x"


def test_failing_batch_fails_its_callers_and_the_worker_survives():
    def flaky(jobs):
        if "bad" in jobs:
            raise RuntimeError("boom")
        return list(jobs)

    async def go():
        batcher = Batcher(flaky, max_size=2, wait_ms=1)
        try:
            await batcher.submit("bad")
            failed = False
        except RuntimeError:
            failed = True
        ok = await batcher.submit("fine")
        batcher.close()
        return failed, ok

    assert _run(go()) == (True, "fine")


def test_engine_runs_on_one_worker_thread_not_the_event_loop():
    threads = set()

    def engine(jobs):
        threads.add(threading.current_thread().name)
        return list(jobs)

    async def go():
        batcher = Batcher(engine, max_size=1, wait_ms=1)
        await asyncio.gather(batcher.submit(1), batcher.submit(2))
        batcher.close()

    _run(go())
    assert threads == {"gpu-worker"}


def test_stats_show_batch_size_histogram_and_queue_wait():
    async def go():
        batcher = Batcher(lambda jobs: list(jobs), max_size=4, wait_ms=100)
        await asyncio.gather(*(batcher.submit(i) for i in range(4)))
        batcher.close()
        return batcher.stats.snapshot()

    snap = _run(go())
    assert snap["batch_size_histogram"] == {"4": 1} and snap["jobs"] == 4
    assert snap["queue_wait_ms"]["p95"] >= snap["queue_wait_ms"]["p50"] >= 0
