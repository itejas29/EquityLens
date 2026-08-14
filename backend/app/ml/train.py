"""Train the secondary ML signal: does a stock beat NIFTY50 over the next
20 trading days? Small, interpretable models only (LogisticRegression
baseline, depth-capped RandomForest) — see app/ml/features.py for the
feature set and its documented fundamentals-snapshot limitation.

Time-based split ONLY: earliest 70% of rows (by date) train, next 15%
validate, final 15% test. No shuffling — shuffling a time series before
splitting would let the model train on rows chronologically after some of
its own test rows, which is exactly the kind of look-ahead this whole
project has been careful to avoid everywhere else.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, confusion_matrix, f1_score, precision_score, recall_score, roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from sqlalchemy.orm import Session

from app.ml.features import FEATURE_COLUMNS, build_feature_dataset
from app.models.stock import Stock
from app.services.backtest import _load_all_price_frames
from app.services.backtest_scoring import compute_point_in_time_universe
from app.services.market_data import fetch_price_history

ARTIFACTS_DIR = Path(__file__).resolve().parent / "artifacts"
RF_MAX_DEPTH = 5
RF_N_ESTIMATORS = 200


def _split(df):
    df = df.sort_values("date").reset_index(drop=True)
    n = len(df)
    train_end = int(n * 0.70)
    val_end = int(n * 0.85)
    return df.iloc[:train_end], df.iloc[train_end:val_end], df.iloc[val_end:]


def _metrics(y_true, y_pred, y_proba) -> dict:
    return {
        "accuracy": round(accuracy_score(y_true, y_pred), 4),
        "precision": round(precision_score(y_true, y_pred, zero_division=0), 4),
        "recall": round(recall_score(y_true, y_pred, zero_division=0), 4),
        "f1": round(f1_score(y_true, y_pred, zero_division=0), 4),
        "roc_auc": round(roc_auc_score(y_true, y_proba), 4) if len(set(y_true)) > 1 else None,
        "confusion_matrix": confusion_matrix(y_true, y_pred).tolist(),
    }


def _rule_based_vs_ml(db: Session, test_df: pd.DataFrame, ml_probability: np.ndarray) -> dict:
    """For every (stock, date) in the test set, compute the point-in-time
    rule-based overall_score (same technical+risk composite the backtest
    uses) and compare its ranking against the ML probability's ranking,
    using the actual 20-day-forward outperformance target as ground truth.
    """
    stocks = db.query(Stock).filter(Stock.is_active == True).all()  # noqa: E712
    raw_frames = _load_all_price_frames(db, stocks)
    benchmark_df = fetch_price_history("^NSEI", period="2y")[["date", "close"]]

    rows = test_df.copy()
    rows["ml_probability"] = ml_probability
    rows["rule_based_score"] = np.nan

    for as_of_date, group in rows.groupby("date"):
        bounded_frames = {sid: df[df["date"] <= as_of_date] for sid, df in raw_frames.items()}
        bounded_bench = benchmark_df[benchmark_df["date"] <= as_of_date]
        snapshot = compute_point_in_time_universe(bounded_frames, bounded_bench)
        for idx in group.index:
            snap = snapshot.get(rows.at[idx, "stock_id"])
            if snap is not None:
                rows.at[idx, "rule_based_score"] = snap.overall_score

    rows = rows.dropna(subset=["rule_based_score"])
    if rows.empty:
        return {"comparable_rows": 0}

    def _precision_at_top_30pct(score_col: str) -> float:
        k = max(1, int(len(rows) * 0.3))
        top = rows.nlargest(k, score_col)
        return round(float(top["target"].mean()), 4)

    rank_correlation = round(float(rows["ml_probability"].corr(rows["rule_based_score"], method="spearman")), 4)

    return {
        "comparable_rows": len(rows),
        "base_rate_target_1": round(float(rows["target"].mean()), 4),
        "ml_precision_at_top_30pct": _precision_at_top_30pct("ml_probability"),
        "rule_based_precision_at_top_30pct": _precision_at_top_30pct("rule_based_score"),
        "spearman_rank_correlation_ml_vs_rule_based": rank_correlation,
    }


def train_and_evaluate(db: Session) -> dict:
    dataset = build_feature_dataset(db)
    train_df, val_df, test_df = _split(dataset)
    print(f"[train] split sizes: train={len(train_df)} val={len(val_df)} test={len(test_df)}")

    X_train, y_train = train_df[FEATURE_COLUMNS], train_df["target"]
    X_val, y_val = val_df[FEATURE_COLUMNS], val_df["target"]
    X_test, y_test = test_df[FEATURE_COLUMNS], test_df["target"]

    majority_class = int(y_train.mode().iloc[0])
    majority_baseline_test_accuracy = round(float((y_test == majority_class).mean()), 4)

    lr_pipeline = Pipeline([("scaler", StandardScaler()), ("clf", LogisticRegression(max_iter=1000))])
    lr_pipeline.fit(X_train, y_train)
    lr_test_pred = lr_pipeline.predict(X_test)
    lr_test_proba = lr_pipeline.predict_proba(X_test)[:, 1]
    lr_metrics = _metrics(y_test, lr_test_pred, lr_test_proba)
    lr_val_accuracy = round(accuracy_score(y_val, lr_pipeline.predict(X_val)), 4)

    rf_model = RandomForestClassifier(
        n_estimators=RF_N_ESTIMATORS, max_depth=RF_MAX_DEPTH, random_state=42, class_weight="balanced"
    )
    rf_model.fit(X_train, y_train)
    rf_test_pred = rf_model.predict(X_test)
    rf_test_proba = rf_model.predict_proba(X_test)[:, 1]
    rf_metrics = _metrics(y_test, rf_test_pred, rf_test_proba)
    rf_val_accuracy = round(accuracy_score(y_val, rf_model.predict(X_val)), 4)

    feature_importances = sorted(
        zip(FEATURE_COLUMNS, rf_model.feature_importances_.tolist()), key=lambda x: x[1], reverse=True
    )
    print("[train] RandomForest feature importances:")
    for name, importance in feature_importances:
        print(f"    {name}: {importance:.4f}")

    print(f"[train] majority-class baseline test accuracy: {majority_baseline_test_accuracy}")
    print(f"[train] LogisticRegression test metrics: {lr_metrics}")
    print(f"[train] RandomForest test metrics: {rf_metrics}")

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    rf_path = ARTIFACTS_DIR / f"rf_{timestamp}.joblib"
    lr_path = ARTIFACTS_DIR / f"lr_{timestamp}.joblib"
    joblib.dump(rf_model, rf_path)
    joblib.dump(lr_pipeline, lr_path)

    results = {
        "trained_at": timestamp,
        "feature_columns": FEATURE_COLUMNS,
        "dataset_rows": len(dataset),
        "train_rows": len(train_df),
        "val_rows": len(val_df),
        "test_rows": len(test_df),
        "train_date_range": [str(train_df["date"].min()), str(train_df["date"].max())],
        "test_date_range": [str(test_df["date"].min()), str(test_df["date"].max())],
        "majority_class": majority_class,
        "majority_baseline_test_accuracy": majority_baseline_test_accuracy,
        "logistic_regression": {"val_accuracy": lr_val_accuracy, "test": lr_metrics},
        "random_forest": {"val_accuracy": rf_val_accuracy, "test": rf_metrics, "feature_importances": feature_importances},
        "rf_artifact": rf_path.name,
        "lr_artifact": lr_path.name,
    }

    # Serve whichever model actually tested better (by ROC-AUC — the most
    # informative metric for a probability output), not just RF by default.
    # On this dataset LR beats RF; forcing RF into production despite that
    # would contradict our own measured evidence.
    rf_auc = rf_metrics["roc_auc"] or 0
    lr_auc = lr_metrics["roc_auc"] or 0
    selected_model = "random_forest" if rf_auc >= lr_auc else "logistic_regression"
    results["selected_model"] = selected_model
    results["selected_artifact"] = rf_path.name if selected_model == "random_forest" else lr_path.name

    selected_proba = rf_test_proba if selected_model == "random_forest" else lr_test_proba
    print("[train] comparing ML probability vs point-in-time rule-based score on the test period...")
    comparison = _rule_based_vs_ml(db, test_df, selected_proba)
    print(f"[train] comparison: {comparison}")
    results["ml_vs_rule_based"] = comparison

    metadata_path = ARTIFACTS_DIR / "latest.json"
    with open(metadata_path, "w") as f:
        json.dump(results, f, indent=2)

    return results


if __name__ == "__main__":
    from app.core.database import SessionLocal

    session = SessionLocal()
    try:
        train_and_evaluate(session)
    finally:
        session.close()
