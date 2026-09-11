"""Where research scripts write their results.

Scripts used to compute `Path(__file__).parents[2] / "docs" / ...`. That is
the repo's docs/ on a laptop, but inside the container the script lives at
/app/scripts/, so parents[2] is `/` and the path became /docs/experiments/...
It worked only while the container ran as root and could create /docs. Once
the image moved to a non-root user, Phase 21 finished all 16 folds (hours of
lab time) and then died with PermissionError at the final write — after the
per-fold detail had been computed and before the summary was printed.

docs/ is not in the image, so inside the container results go under /app
(owned by appuser) and are copied out with `docker cp`.
"""

import os
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[2]   # backend/ locally, /app in the image
_REPO_DOCS = _BACKEND.parent / "docs"


def experiment_dir(name: str = "") -> Path:
    """Results directory for one experiment, created if missing.

    EXPERIMENT_OUT_DIR overrides everything. Otherwise the repo's
    docs/experiments when it exists (a checkout), else /app/experiment_results
    (the container). Created here, at startup, so an unwritable destination
    fails before the run rather than after it.
    """
    override = os.environ.get("EXPERIMENT_OUT_DIR")
    if override:
        base = Path(override)
    elif _REPO_DOCS.is_dir():
        base = _REPO_DOCS / "experiments"
    else:
        base = _BACKEND / "experiment_results"
    out = base / name if name else base
    out.mkdir(parents=True, exist_ok=True)
    return out
