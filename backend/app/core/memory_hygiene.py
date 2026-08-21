"""Periodic memory release for long loops over the ~500-stock universe.

Each iteration of these loops (incremental.py, universe.py, daily_signals.py)
allocates and discards a handful of pandas DataFrames — one stock's price
history, one stock's computed indicators. Individually these are small and
correctly garbage-collected, but glibc's malloc does not hand freed arenas
back to the OS by default, so RSS creeps upward across hundreds of iterations
even though the live working set stays roughly constant. Confirmed live: a
genuine 501-stock incremental run measured 412MB peak RSS against Render's
512MB limit, and this pattern is the most likely explanation given the
upsert functions use Core-level inserts (no ORM identity-map growth) and
bounding the per-stock query window only reduced the peak by ~16MB.

`malloc_trim` is glibc-specific (Linux) and a no-op-safe call elsewhere; the
container this runs in is always Linux, but the guard keeps a local non-Linux
dev shell from erroring.
"""

import ctypes
import gc
import logging

logger = logging.getLogger(__name__)

try:
    _libc = ctypes.CDLL("libc.so.6")
except OSError:
    _libc = None


def trim_every(counter: int, every: int = 50) -> None:
    """Call after processing each item in a long per-stock loop. Every `every`
    calls, forces a GC pass and asks glibc to release freed arenas to the OS.
    """
    if counter % every != 0:
        return
    gc.collect()
    if _libc is not None:
        try:
            _libc.malloc_trim(0)
        except Exception:
            logger.debug("malloc_trim unavailable", exc_info=True)
