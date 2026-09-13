"""Generate the momentum_v1.0 verdict artifact from a test run and the evidence bundle.

    python scripts/generate_strategy_verdict.py [--postgres-url URL] [--junit PATH] [--allow-dirty]

1. Refuses a dirty working tree (unless --allow-dirty): the verdict pins a code
   revision, and a revision the tested code is not is worse than none.
2. Runs the suite with a junit report (or reads --junit). Pass --postgres-url
   so the row-lock test runs: skipped, it leaves concurrent_portfolio_updates
   NOT_EVALUATED, which is correct but blocks a measurement PASS.
3. Computes the verdict with services/strategy_verdict.compute_verdict.
4. Writes backend/app/core/verdict_artifacts/momentum_v1.0.verdict.json.

tests/test_strategy_verdict_artifact.py is EXCLUDED from this run and says so in
the artifact. It verifies the committed artifact, so it cannot be an input to
the artifact — including it would make the first generation fail its own check.
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parent
sys.path.insert(0, str(BACKEND))
os.environ.setdefault("DATABASE_URL", "postgresql+psycopg2://unused:unused@localhost/unused")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")
os.environ.setdefault("JWT_SECRET_KEY", "verdict-generation-does-not-use-this-secret")

from app.core.canonical import sha256_hex  # noqa: E402
from app.services.strategy_verdict import compute_verdict  # noqa: E402

EVIDENCE = BACKEND / "app/core/verdict_artifacts/momentum_v1.0.evidence.json"
ARTIFACT = BACKEND / "app/core/verdict_artifacts/momentum_v1.0.verdict.json"
SELF_CHECK = "tests/test_strategy_verdict_artifact.py"

# Worst outcome wins when a function has several parametrized cases.
_RANK = {"passed": 0, "skipped": 1, "xfailed": 2, "failed": 3}


def parse_junit(xml_text: str) -> tuple[dict, dict]:
    """junit XML -> ({node_id: {status, message}}, suite totals).

    Node ids are file::function. pytest reports an xfail as <skipped
    type="pytest.xfail">; a strict XPASS as a <failure>. Both matter and are
    distinguished here — an xfail is a known defect, not a skip.
    """
    root = ET.fromstring(xml_text)
    outcomes: dict[str, dict] = {}
    suite = {"tests": 0, "passed": 0, "failures": 0, "errors": 0, "skipped": 0, "xfailed": 0}
    for case in root.iter("testcase"):
        classname, name = case.get("classname", ""), case.get("name", "")
        node = classname.replace(".", "/") + ".py::" + name.split("[", 1)[0]
        suite["tests"] += 1
        failure, error, skipped = case.find("failure"), case.find("error"), case.find("skipped")
        if error is not None:
            status, message = "failed", error.get("message", "")
            suite["errors"] += 1
        elif failure is not None:
            status, message = "failed", failure.get("message", "")
            suite["failures"] += 1
        elif skipped is not None and skipped.get("type") == "pytest.xfail":
            status, message = "xfailed", skipped.get("message", "")
            suite["xfailed"] += 1
        elif skipped is not None:
            status, message = "skipped", skipped.get("message", "")
            suite["skipped"] += 1
        else:
            status, message = "passed", ""
            suite["passed"] += 1
        prev = outcomes.get(node)
        if prev is None or _RANK[status] > _RANK[prev["status"]]:
            outcomes[node] = {"status": status, "message": message.strip()}
    return outcomes, suite


def _git(*args) -> str:
    return subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True, check=True).stdout.strip()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--junit")
    ap.add_argument("--postgres-url")
    ap.add_argument("--allow-dirty", action="store_true")
    args = ap.parse_args()

    dirty = [line for line in _git("status", "--porcelain").splitlines()
             if not line.endswith(str(ARTIFACT.relative_to(REPO)))]
    if dirty and not args.allow_dirty:
        print("refusing: working tree is dirty, so the tested code is not the pinned revision:\n  "
              + "\n  ".join(dirty), file=sys.stderr)
        return 2
    revision = _git("rev-parse", "HEAD") + ("-dirty" if dirty else "")

    if args.junit:
        xml_text = Path(args.junit).read_text()
    else:
        with tempfile.TemporaryDirectory() as tmp:
            report = Path(tmp) / "junit.xml"
            env = {**os.environ}
            if args.postgres_url:
                env["AUDIT_POSTGRES_URL"] = args.postgres_url
            subprocess.run(
                [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                 f"--junitxml={report}", "--ignore", SELF_CHECK],
                cwd=BACKEND, env=env,
            )
            xml_text = report.read_text()

    outcomes, suite = parse_junit(xml_text)
    suite["excluded"] = [f"{SELF_CHECK}: verifies this artifact, so cannot be an input to it"]

    evidence = json.loads(EVIDENCE.read_text())
    verdict = compute_verdict(
        evidence=evidence, evidence_sha256=sha256_hex(evidence), outcomes=outcomes, suite=suite,
        code_revision=revision, generated_at=datetime.now(timezone.utc).isoformat(),
    )
    ARTIFACT.write_text(verdict.to_json())

    print(f"wrote {ARTIFACT.relative_to(REPO)}")
    print(f"  revision     {verdict.code_revision}")
    print(f"  snapshot     {verdict.data_snapshot}")
    print(f"  measurement  {verdict.measurement_verdict}")
    print(f"  edge         {verdict.edge_verdict}")
    print(f"  promotion    {verdict.promotion_verdict}")
    print(f"  suite        {suite}")
    print(f"\n{verdict.summary}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
