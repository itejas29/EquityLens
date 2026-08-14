"""Load the model app/ml/train.py selected (whichever tested better — see
docs/ml_results.md) and score individual stocks. This is a secondary
signal: ml_probability is an additional field on the recommendation
response, never blended into overall_score.
"""

import json
from pathlib import Path

import joblib
import pandas as pd
from sqlalchemy.orm import Session

from app.ml.features import FEATURE_COLUMNS, build_latest_features_row

ARTIFACTS_DIR = Path(__file__).resolve().parent / "artifacts"

_model_cache: dict = {}


def _load_selected_model():
    if "model" in _model_cache:
        return _model_cache["model"]

    metadata_path = ARTIFACTS_DIR / "latest.json"
    if not metadata_path.exists():
        return None

    with open(metadata_path) as f:
        metadata = json.load(f)

    artifact_path = ARTIFACTS_DIR / metadata["selected_artifact"]
    if not artifact_path.exists():
        return None

    model = joblib.load(artifact_path)
    _model_cache["model"] = model
    return model


def predict_probability(db: Session, stock_id: int) -> float | None:
    """P(stock outperforms NIFTY50 over the next 20 trading days), or None
    if no trained model is available or the stock's current data doesn't
    cover all required features (e.g. no fundamentals snapshot)."""
    model = _load_selected_model()
    if model is None:
        return None

    row = build_latest_features_row(db, stock_id)
    if row is None:
        return None

    features = pd.DataFrame([row])[FEATURE_COLUMNS]
    probability = model.predict_proba(features)[0, 1]
    return round(float(probability), 4)
