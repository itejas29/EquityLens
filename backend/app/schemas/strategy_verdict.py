from typing import Literal

from pydantic import BaseModel


class VerdictCheck(BaseModel):
    name: str
    status: Literal["PASS", "FAIL", "NOT_EVALUATED"]
    required: bool
    detail: str
    evidence: list[str]


class VerdictTestSuite(BaseModel):
    tests: int
    passed: int
    failures: int
    errors: int
    skipped: int
    xfailed: int
    excluded: list[str] = []


class StrategyVerdictResponse(BaseModel):
    """The committed verdict artifact, served as generated.

    The three verdict fields are Literal-typed to the same controlled
    vocabularies as core/verdict.py, so a value outside them cannot leave the
    API even if the artifact somehow contained one.
    """

    strategy_id: str
    strategy_version: str
    measurement_verdict: Literal["PASS", "FAIL", "BLOCKED"]
    edge_verdict: Literal["PASS", "FAIL", "INCONCLUSIVE", "BLOCKED"]
    promotion_verdict: Literal["APPROVED", "BLOCKED", "DEFERRED"]
    generated_at: str
    code_revision: str
    data_snapshot: str
    content_sha256: str
    summary: str
    measurement_checks: list[VerdictCheck]
    edge_checks: list[VerdictCheck]
    governance_checks: list[VerdictCheck]
    edge_figures: dict
    thresholds: dict[str, float]
    known_biases: list[dict]
    test_suite: VerdictTestSuite
    live_signals_through: str | None
    report_path: str
