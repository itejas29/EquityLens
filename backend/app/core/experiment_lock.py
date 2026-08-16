"""Advisory lock preventing production jobs from mutating research data.

Phase 15's first run was corrupted mid-flight: the weekly universe-rebuild
scheduler fired, changed universe membership from 500 to 501 stocks, and the
run crashed at fold 12 with a stale cached snapshot referencing a stock the
current frames no longer had. A long research run must not be able to have the
ground shift under it.

This is a filesystem lock rather than a DB row on purpose — it has to work even
when the API process is down, and it must be trivially inspectable and
removable by a human who finds a stale one.

Production loops call `is_locked()` and skip their mutation when a research run
holds the lock. Research scripts wrap themselves in `experiment_lock(...)`.
The lock records who holds it and since when, so a stale lock is diagnosable
rather than mysterious.
"""

import json
import logging
import os
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

LOCK_PATH = Path(__file__).resolve().parents[2] / ".experiment.lock"


def is_locked() -> bool:
    return LOCK_PATH.exists()


def lock_info() -> dict | None:
    if not LOCK_PATH.exists():
        return None
    try:
        return json.loads(LOCK_PATH.read_text())
    except Exception:
        return {"malformed": True}


@contextmanager
def experiment_lock(name: str, force: bool = False):
    """Hold the lock for the duration of a research run.

    Refuses to start if another run holds it, unless `force` — silently
    stealing a peer's lock is how two experiments end up interleaving writes.
    """
    if LOCK_PATH.exists() and not force:
        raise RuntimeError(
            f"Another experiment holds the lock: {lock_info()}. "
            f"Wait for it, or delete {LOCK_PATH} if it is stale."
        )

    LOCK_PATH.write_text(
        json.dumps(
            {"experiment": name, "pid": os.getpid(), "started_at": datetime.now(timezone.utc).isoformat()},
            indent=2,
        )
    )
    logger.info("Experiment lock acquired: %s (pid %d)", name, os.getpid())
    try:
        yield
    finally:
        try:
            LOCK_PATH.unlink()
            logger.info("Experiment lock released: %s", name)
        except FileNotFoundError:
            logger.warning("Experiment lock was already gone at release")
