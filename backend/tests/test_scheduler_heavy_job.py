"""Heavy batch jobs must not overlap, and "one is running" must stay true
until the last one finishes.

This was a boolean — `_heavy_job_active = True` on entry, `False` in the
finally — which the commit that added it described as a mutex. It is not one,
and it failed in two distinct ways. Both are asserted here.

Why it matters on this deployment: daily_price_update and
weekly_universe_rebuild both fire at REBUILD_HOUR:REBUILD_MINUTE, so on the
weekly rebuild day they start together. daily_signals_loop's recovery path also
runs _run_incremental_update. Two full 500-stock incremental passes overlapping
is double the memory and double the yfinance load, on a box with four prior
memory incidents — which is the exact thing the flag was added to prevent.
"""

import asyncio

import pytest

from app.core import scheduler


@pytest.fixture(autouse=True)
def fresh_lock(monkeypatch):
    """A lock per test. asyncio.Lock binds to the loop that first awaits it,
    and pytest gives each test its own loop."""
    monkeypatch.setattr(scheduler, "_heavy_lock", asyncio.Lock())


def _run(coro):
    return asyncio.run(coro)


def test_two_heavy_jobs_do_not_overlap():
    """The first failure: the flag never serialised anything. It only told the
    light loops to back off, while both heavy jobs ran regardless."""
    concurrent = 0
    peak = 0

    async def job(name, hold):
        nonlocal concurrent, peak
        async with scheduler._heavy_job(name):
            concurrent += 1
            peak = max(peak, concurrent)
            await asyncio.sleep(hold)
            concurrent -= 1

    async def main():
        await asyncio.gather(job("a", 0.05), job("b", 0.05), job("c", 0.05))

    _run(main())
    assert peak == 1, f"{peak} heavy jobs ran at once"


def test_the_running_flag_stays_true_until_the_last_job_finishes():
    """The second failure: whichever job finished FIRST set the flag back to
    False while the other was still running, so the light loops resumed
    ticking mid-job — undoing the protection entirely."""
    observed: list[bool] = []

    async def short():
        async with scheduler._heavy_job("short"):
            await asyncio.sleep(0.01)

    async def long_():
        async with scheduler._heavy_job("long"):
            await asyncio.sleep(0.08)
            # Sampled from inside the second job, after the first has finished.
            observed.append(scheduler.heavy_job_running())

    async def main():
        await asyncio.gather(short(), long_())

    _run(main())
    assert observed == [True], "a finished job cleared the running flag for one still going"


def test_the_flag_clears_once_everything_is_done():
    async def main():
        async with scheduler._heavy_job("x"):
            assert scheduler.heavy_job_running() is True
        return scheduler.heavy_job_running()

    assert _run(main()) is False


def test_an_exception_inside_a_job_still_releases_the_lock():
    """A heavy job that raises must not wedge every other loop forever."""
    async def main():
        with pytest.raises(RuntimeError):
            async with scheduler._heavy_job("boom"):
                raise RuntimeError("job failed")
        assert scheduler.heavy_job_running() is False
        # And the lock is genuinely reusable, not merely reported as free.
        async with scheduler._heavy_job("after"):
            return scheduler.heavy_job_running()

    assert _run(main()) is True


def test_the_light_loops_read_the_lock_not_a_variable():
    """price_refresh_loop, fast_quote_loop and ai_trading_loop gate on this.
    Reading a module-level bool is what let a stale value through."""
    import inspect

    source = inspect.getsource(scheduler)
    assert "heavy_job_running()" in source
    # The old flag may appear in the explanatory comment, but never as code.
    for line in source.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        assert "_heavy_job_active" not in stripped, f"live reference to the old flag: {stripped}"
