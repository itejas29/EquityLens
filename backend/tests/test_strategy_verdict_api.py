"""GET /api/v1/strategy-verdict serves the committed artifact and nothing else."""

import json

from app.api.v1 import strategy_verdict as endpoint
from app.core.verdict import StrategyVerdict


def _artifact() -> StrategyVerdict:
    return StrategyVerdict.from_json(endpoint.ARTIFACT.read_text())


def test_the_endpoint_serves_the_artifact_verdict(client):
    resp = client.get("/api/v1/strategy-verdict")
    assert resp.status_code == 200, resp.text
    body, v = resp.json(), _artifact()
    assert (body["measurement_verdict"], body["edge_verdict"], body["promotion_verdict"]) == (
        v.measurement_verdict, v.edge_verdict, v.promotion_verdict)
    assert body["content_sha256"] == v.content_sha256()
    assert body["code_revision"] == v.code_revision and body["data_snapshot"] == v.data_snapshot
    assert body["summary"] == v.summary


def test_a_blocked_promotion_arrives_with_the_checks_that_block_it(client):
    body = client.get("/api/v1/strategy-verdict").json()
    failing = [c for c in body["measurement_checks"] + body["edge_checks"] if c["status"] == "FAIL"]
    # Unconditional: no early return that would let this pass vacuously if the
    # verdict changed. BLOCKED must always come with a stated reason.
    assert body["promotion_verdict"] != "BLOCKED" or failing, (
        "a BLOCKED promotion was served without any failing check to explain it")
    assert all(c["detail"] for c in failing)


def test_no_request_parameter_can_change_a_verdict(client):
    plain = client.get("/api/v1/strategy-verdict").json()
    pushed = client.get("/api/v1/strategy-verdict", params={
        "measurement_verdict": "PASS", "edge_verdict": "PASS", "promotion_verdict": "APPROVED",
    }).json()
    assert pushed == plain


def test_there_is_no_write_path(client):
    for method in ("post", "put", "patch", "delete"):
        assert getattr(client, method)("/api/v1/strategy-verdict").status_code == 405


def test_an_artifact_edited_after_generation_is_not_served(client, tmp_path, monkeypatch):
    payload = json.loads(endpoint.ARTIFACT.read_text())
    payload["summary"] = payload["summary"].replace("Promotion BLOCKED.", "Promotion looks fine.")
    tampered = tmp_path / "verdict.json"
    tampered.write_text(json.dumps(payload))
    monkeypatch.setattr(endpoint, "ARTIFACT", tampered)

    resp = client.get("/api/v1/strategy-verdict")
    assert resp.status_code == 500
    # The app-wide handler shapes errors as {"error": {"message", "status_code"}}.
    assert "integrity" in resp.json()["error"]["message"]
