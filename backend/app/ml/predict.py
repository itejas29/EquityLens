"""Load the model app/ml/train.py selected (whichever tested better — see
docs/ml_results.md) and score individual stocks. This is a secondary
signal: ml_probability is an additional field on the recommendation
response, never blended into overall_score.

SERVING GATE: a probability is only returned when the trained artifact's own
measured test ROC-AUC clears MIN_SERVABLE_ROC_AUC. On the 80,846-row / 375-stock
dataset the best model scored 0.5136 — statistically indistinguishable from a
coin flip — so nothing is served today and `ml_probability` reads as null. That
is the intended behaviour, not a bug: rendering a near-random number next to a
buy call would make it look like corroborating evidence when it carries no
measured information. The gate is self-enforcing, so a future retrain that
genuinely clears the bar starts serving automatically with no code change.
"""

import json
import logging
from pathlib import Path

import joblib
import pandas as pd
from sqlalchemy.orm import Session

from app.ml.features import FEATURE_COLUMNS, build_latest_features_frame

logger = logging.getLogger(__name__)

ARTIFACTS_DIR = Path(__file__).resolve().parent / "artifacts"

# 0.55 is a deliberately modest bar — not "good", just far enough from 0.50 to
# be plausibly signal rather than noise on a test set of this size.
MIN_SERVABLE_ROC_AUC = 0.55

_model_cache: dict = {}
_frame_cache: dict = {}


def _load_selected_model():
    if "model" in _model_cache:
        return _model_cache["model"]

    metadata_path = ARTIFACTS_DIR / "latest.json"
    if not metadata_path.exists():
        return None

    with open(metadata_path) as f:
        metadata = json.load(f)

    # Read the selected model's OWN reported test AUC and refuse to serve it if
    # it has no measured edge. Checked here rather than at training time so an
    # artifact that was fine when trained cannot keep being served after a
    # retrain records a worse score.
    selected = metadata.get("selected_model")
    measured = (metadata.get(selected) or {}).get("test", {}).get("roc_auc")
    if measured is None or measured < MIN_SERVABLE_ROC_AUC:
        logger.info(
            "ML predictions disabled — %s test ROC-AUC %.4f is below the %.2f serving bar",
            selected, measured if measured is not None else float("nan"), MIN_SERVABLE_ROC_AUC,
        )
        _model_cache["model"] = None
        return None

    artifact_path = ARTIFACTS_DIR / metadata["selected_artifact"]
    if not artifact_path.exists():
        return None

    model = joblib.load(artifact_path)
    _model_cache["model"] = model
    return model


def _latest_frame(db: Session) -> pd.DataFrame:
    """Cached latest-cross-section. Built once per process rather than per call:
    the cross-sectional rank features need every stock's latest row, so building
    it per stock would rebuild the whole panel for each of 500 lookups.
    """
    if "frame" not in _frame_cache:
        _frame_cache["frame"] = build_latest_features_frame(db)
    return _frame_cache["frame"]


def invalidate_feature_cache() -> None:
    """Call after ingesting or refreshing stocks — the cross-section has moved."""
    _frame_cache.pop("frame", None)


def predict_probability(db: Session, stock_id: int) -> float | None:
    """P(stock outperforms NIFTY50 over the next 20 trading days), or None when
    no model clears the serving bar, or this stock's latest row is missing any
    required feature (no fundamentals snapshot, too little history for the
    120-day relative-strength window, etc.)."""
    model = _load_selected_model()
    if model is None:
        return None

    frame = _latest_frame(db)
    if frame.empty or stock_id not in frame.index:
        return None

    features = frame.loc[[stock_id], FEATURE_COLUMNS]
    probability = model.predict_proba(features)[0, 1]
    return round(float(probability), 4)
