"""The due-diligence endpoint.

This page is the product being sold, so the property that matters is not that
it renders — it is that it cannot quietly become flattering. Two things would
do that: a figure that stops being computed from the live database, and a
verdict record that drops the phases which refuted their own hypothesis.
"""

import pytest


def test_it_is_reachable_without_a_token(client):
    """A due-diligence surface a reviewer cannot open is not one."""
    assert client.get("/api/v1/methodology").status_code == 200


def test_every_section_is_present(client):
    body = client.get("/api/v1/methodology").json()
    for key in ("strategy", "protocol", "universe", "integrity",
                "ml_gate", "live_arm", "phases", "track_record"):
        assert key in body, f"missing section: {key}"


def test_the_strategy_reported_is_the_frozen_one(client):
    """If this ever disagrees with core/v1_strategy, the page is describing a
    configuration production does not run — which is the exact failure the
    frozen-strategy module was created to prevent."""
    from app.core.v1_strategy import V1, V1_VERSION

    s = client.get("/api/v1/methodology").json()["strategy"]
    assert s["version"] == V1_VERSION
    assert s["params"]["atr_stop_multiplier"] == V1.atr_stop_multiplier
    assert s["params"]["use_support_stop"] == V1.use_support_stop
    assert s["params"]["rebalance_frequency"] == V1.rebalance_frequency
    assert s["params"]["ranking"] == V1.ranking_engine


def test_the_refuted_phases_are_not_quietly_dropped(client):
    """The point of the record is the failures. A version of this page that
    listed only the phases that held would be marketing."""
    phases = client.get("/api/v1/methodology").json()["phases"]
    verdicts = [p["verdict"] for p in phases]

    assert "REFUTED" in verdicts, "no refuted phase survived into the payload"
    assert verdicts.count("REFUTED") >= 4
    assert {p["phase"] for p in phases} >= {"Phase 14", "Phase 18", "Phase 19", "Phase 20"}


def test_every_phase_names_the_directory_its_numbers_came_from(client):
    """A claim without a source is an assertion. Each row has to point at the
    committed experiment output so it can be checked."""
    for p in client.get("/api/v1/methodology").json()["phases"]:
        assert p["source"].startswith("docs/experiments/"), p


def test_the_live_arm_is_reported_against_its_benchmark(client):
    """Return on its own is not a result — a positive number in a rising market
    says nothing. The comparison has to travel with it."""
    arm = client.get("/api/v1/methodology").json()["live_arm"]
    for key in ("return_pct", "benchmark_return_pct", "vs_benchmark_pp",
                "folds_beating_benchmark", "folds_total"):
        assert key in arm
    assert arm["vs_benchmark_pp"] == pytest.approx(
        arm["return_pct"] - arm["benchmark_return_pct"], abs=0.01)


def test_the_ml_gate_reports_its_own_threshold_and_whether_it_is_serving(client):
    from app.ml.predict import MIN_SERVABLE_ROC_AUC

    gate = client.get("/api/v1/methodology").json()["ml_gate"]
    assert gate["serving_threshold_roc_auc"] == MIN_SERVABLE_ROC_AUC
    assert "is_serving" in gate
    # Whatever the measured value, serving must follow from it and nothing else.
    if gate["measured_test_roc_auc"] is not None:
        assert gate["is_serving"] == (gate["measured_test_roc_auc"] >= MIN_SERVABLE_ROC_AUC)


def test_the_universe_reports_the_retained_delisted_names(client):
    """The inactive count is what quantifies survivorship exposure. Reporting
    only the active count would hide the very thing the section discloses."""
    u = client.get("/api/v1/methodology").json()["universe"]
    assert "inactive_retained" in u
    assert "active" in u and "bars" in u


def test_it_survives_an_empty_database(client, db_session):
    """A fresh deployment must not 500 on its own showcase page."""
    resp = client.get("/api/v1/methodology")
    assert resp.status_code == 200
    body = resp.json()
    assert body["universe"]["active"] == 0
    assert body["integrity"]["corporate_action_shaped"] == 0
    # The research record is a transcription, so it is present regardless.
    assert len(body["phases"]) >= 6


def test_a_redis_outage_does_not_break_the_page(client, fake_redis):
    from tests.conftest import redis_down

    with redis_down(fake_redis):
        assert client.get("/api/v1/methodology").status_code == 200
