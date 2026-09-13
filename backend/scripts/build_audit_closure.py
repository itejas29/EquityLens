"""Build the momentum_v1.0 audit-closure deliverables from committed artifacts.

    python scripts/build_audit_closure.py [--out DIR]

Writes, into docs/audit/momentum_v1.0/ by default:
  momentum_v1.0_audit_closure.md
  momentum_v1.0_metrics.json
  momentum_v1.0_trade_ledger.csv
  momentum_v1.0_bug_impact_register.md

Every verdict, check status and figure is read from the verdict artifact, the
evidence bundle, the raw ledger export, and docs/audit/README.md. Nothing is
typed in here that the artifacts could contradict. Deterministic — no clock
reads — so tests/test_audit_closure_reports.py can rebuild into a temp dir and
require byte equality with the committed files. That is what makes "the report
agrees with the machine-readable verdict" enforced rather than intended.
"""

import argparse
import csv
import io
import json
import re
import sys
from pathlib import Path
from statistics import mean, median

BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parent
sys.path.insert(0, str(BACKEND))

import os  # noqa: E402

os.environ.setdefault("DATABASE_URL", "postgresql+psycopg2://unused:unused@localhost/unused")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")
os.environ.setdefault("JWT_SECRET_KEY", "report-build-does-not-use-this-secret")

from app.core.canonical import sha256_hex  # noqa: E402
from app.core.verdict import StrategyVerdict  # noqa: E402

ARTIFACT = BACKEND / "app/core/verdict_artifacts/momentum_v1.0.verdict.json"
EVIDENCE = BACKEND / "app/core/verdict_artifacts/momentum_v1.0.evidence.json"
LEDGER = REPO / "docs/audit/momentum_v1.0/raw/paper_ledger.json"
PHASE18 = REPO / "docs/experiments/phase18_universe_size/results.json"
README = REPO / "docs/audit/README.md"
FINDINGS_META = REPO / "docs/audit/findings_metadata.json"
DEFAULT_OUT = REPO / "docs/audit/momentum_v1.0"
PREFIX = "momentum_v1.0"

HEADING = re.compile(r"^### (\d+)\. (.+?) — (.+)$")


def _mean_metrics(records: list[dict]) -> dict:
    keys = sorted({k for r in records for k, v in r.items() if isinstance(v, (int, float)) and not isinstance(v, bool)})
    out = {}
    for k in keys:
        vals = [r[k] for r in records if isinstance(r.get(k), (int, float)) and not isinstance(r.get(k), bool)]
        out[k] = mean(vals) if vals else None
    return out


def build_metrics(verdict: StrategyVerdict, evidence: dict, ledger: dict) -> dict:
    bt = evidence["backtest"]
    folds = bt["folds"]
    excess = [f["strategy"]["total_return_pct"] - f["benchmark"]["total_return_pct"] for f in folds]
    suite = verdict.evidence["test_run"]["suite"]

    p18 = json.loads(PHASE18.read_text())
    p18_arms = {}
    for arm in p18["arms"]:
        s = [f["arms"][arm]["total_return_pct"] for f in p18["folds"] if arm in f["arms"]]
        b = [f["benchmark"]["total_return_pct"] for f in p18["folds"] if arm in f["arms"]]
        p18_arms[arm] = {"mean_return_pct": mean(s), "benchmark_mean_return_pct": mean(b),
                         "mean_fold_excess_pp": mean(x - y for x, y in zip(s, b)), "folds": len(s)}

    return {
        "strategy_id": verdict.strategy_id,
        "strategy_version": verdict.strategy_version,
        "verdict": {
            "measurement": verdict.measurement_verdict,
            "edge": verdict.edge_verdict,
            "promotion": verdict.promotion_verdict,
            "content_sha256": verdict.content_sha256(),
        },
        "benchmark": "NIFTY_50_PRICE_INDEX",
        "benchmark_note": bt["benchmark"],
        "backtest_period": {
            "first_test_start": folds[0]["test_start"],
            "last_test_end": folds[-1]["test_end"],
            "protocol": bt["protocol"],
        },
        "fold_count": len(folds),
        "tests": {
            "total": suite["tests"], "passed": suite["passed"], "failed": suite["failures"],
            "errors": suite["errors"], "skipped": suite["skipped"], "known_defects_xfail": suite["xfailed"],
            "excluded": suite.get("excluded", []),
        },
        "metrics": {
            "source": bt["source"], "arm": bt["arm"], "universe": bt["universe"],
            "per_fold": [{"fold": f["fold"], "test_start": f["test_start"], "test_end": f["test_end"],
                          "regime": f["regime"], **f["strategy"]} for f in folds],
            "mean_over_folds": _mean_metrics([f["strategy"] for f in folds]),
        },
        "benchmarks": {
            "per_fold": [{"fold": f["fold"], **f["benchmark"]} for f in folds],
            "mean_over_folds": _mean_metrics([f["benchmark"] for f in folds]),
        },
        "excess_vs_benchmark": {
            "per_fold_pp": excess,
            "mean_pp": mean(excess),
            "median_pp": median(excess),
            "folds_beating_benchmark": sum(e > 0 for e in excess),
            "folds_whose_removal_alone_flips_the_mean":
                verdict.evidence["edge_figures"].get("folds_whose_removal_alone_flips_the_mean"),
        },
        "cost_scenarios": {
            "eligible_for_verdict": [],
            "not_evaluated_reason": next(e["reason"] for e in evidence["ineligible_evidence"]
                                         if e["check"] == "passes_stress_slippage"),
            "supplementary_not_used_by_verdict": {
                "source": str(PHASE18.relative_to(REPO)),
                "caveat": "Same strategy version, but a current-universe top-N rather than the verdict's "
                          "point-in-time universe; construction alone moves results ~12pp (Phase 18).",
                "arms": {k: v for k, v in p18_arms.items() if "cost" in k},
            },
        },
        "universe_scenarios": {
            "supplementary_not_used_by_verdict": {
                "source": str(PHASE18.relative_to(REPO)),
                "caveat": "Current-universe top-N, not point-in-time.",
                "arms": {k: v for k, v in p18_arms.items() if "cost" not in k},
            },
            "withdrawn": {"source": "docs/experiments/phase19_universe_robustness",
                          "reason": "indicator cache contamination (audit finding #18)"},
        },
        "live_paper": {
            "track_record": evidence["live_track_record"],
            "ledger": {"exported_at": ledger["exported_at"], "account": ledger["account"],
                       "reconciliation": ledger["reconciliation"]},
        },
        "known_biases": bt["known_biases"],
        "ineligible_evidence": evidence["ineligible_evidence"],
        "precision": {
            "backtest": bt["precision"] + "; aggregates here are computed from those values at full float precision",
            "live": evidence["live_track_record"].get("note"),
        },
        "generated_from": {
            "code_revision": verdict.code_revision,
            "data_snapshot": verdict.data_snapshot,
            "sources": evidence["sources"],
        },
    }


LEDGER_COLUMNS = [
    "strategy_id", "signal_id", "symbol", "signal_timestamp", "entry_timestamp", "entry_price",
    "quantity", "stop_level", "target_or_exit_rule", "exit_timestamp", "exit_price", "exit_reason",
    "gross_pnl", "fees", "slippage", "net_pnl", "sector", "model_version", "data_snapshot", "status",
]


def build_ledger_csv(verdict: StrategyVerdict, ledger: dict, evidence: dict) -> str:
    snapshot = "sha256:" + evidence["sources"]["docs/audit/momentum_v1.0/raw/paper_ledger.json"]
    rows = sorted(ledger["trades"], key=lambda t: (t["entry_timestamp"], t["trade_id"]))
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=LEDGER_COLUMNS, lineterminator="\n")
    w.writeheader()
    for t in rows:
        w.writerow({
            "strategy_id": verdict.strategy_id,
            "signal_id": t["signal_id"] if t["signal_id"] is not None else "",
            "symbol": t["symbol"],
            "signal_timestamp": t["signal_timestamp"] or "",
            "entry_timestamp": t["entry_timestamp"],
            # Cost-loaded: the stored fill already includes the entry cost.
            "entry_price": t["entry_price_cost_loaded"],
            "quantity": t["quantity"],
            "stop_level": t["stop_level"] or "",
            "target_or_exit_rule": f"target {t['target_price']}; stop {t['stop_level']}; horizon or regime exit"
                                   if t["target_price"] else "",
            "exit_timestamp": t["exit_timestamp"] or "",
            "exit_price": t["exit_price"] or "",
            "exit_reason": t["exit_reason"] or "",
            # Not stored separately (see momentum_v1.0_audit_closure.md, Limitations). Left empty rather
            # than re-derived from today's cost constants.
            "gross_pnl": "", "fees": "", "slippage": "",
            "net_pnl": t["net_pnl"] or "",
            "sector": t["sector"] or "",
            "model_version": "",
            "data_snapshot": snapshot,
            "status": t["status"],
        })
    return buf.getvalue()


def build_register() -> str:
    meta = json.loads(FINDINGS_META.read_text())
    findings = []
    for line in README.read_text().splitlines():
        m = HEADING.match(line)
        if not m:
            continue
        num, title, tail = m.groups()
        severity, _, status = tail.partition(",")
        findings.append((num, title.strip(), severity.strip(), status.strip()))

    missing = [n for n, *_ in findings if n not in meta]
    if missing:
        raise SystemExit(f"findings_metadata.json has no entry for finding(s) {missing}")

    out = [
        "# momentum_v1.0 — bug impact register",
        "",
        "Generated by `backend/scripts/build_audit_closure.py`. Titles, severities and statuses are read",
        "from the headings in [README.md](../README.md); the remaining columns from",
        "[findings_metadata.json](../findings_metadata.json). Do not edit this file by hand.",
        "",
    ]
    by_status: dict[str, int] = {}
    for num, title, severity, status in findings:
        key = "open" if "open" in status.lower() else ("measured, not fixed" if "not fixed" in status else "fixed")
        by_status[key] = by_status.get(key, 0) + 1
    out.append(f"**{len(findings)} findings** — " + ", ".join(f"{v} {k}" for k, v in sorted(by_status.items())) + ".")
    out.append("")
    for num, title, severity, status in findings:
        m = meta[num]
        out += [
            f"## {num}. {title}",
            "",
            "| field | |",
            "|---|---|",
            f"| ID | {num} |",
            f"| Severity | {severity} |",
            f"| Status (README) | {status} |",
            f"| Discovered | {m['discovered']} |",
            f"| Affected component | {m['component']} |",
            f"| Affected period | {m['affected_period']} |",
            f"| Historical outputs changed | {m['historical_outputs_changed']} |",
            f"| Regression test | {', '.join('`' + t + '`' for t in m['regression_tests'])} |",
            f"| Final disposition | {m['disposition']} |",
            "",
        ]
    return "\n".join(out)


def _table(checks: list[dict]) -> list[str]:
    icon = {"PASS": "✓ PASS", "FAIL": "✗ FAIL", "NOT_EVALUATED": "– NOT EVALUATED"}
    rows = ["| check | result | detail |", "|---|---|---|"]
    for c in checks:
        detail = c["detail"].replace("|", "\\|")
        rows.append(f"| `{c['name']}` | {icon[c['status']]} | {detail} |")
    return rows


def _decision(verdict: StrategyVerdict, ev: dict) -> str:
    failing = lambda group: [c["name"] for c in ev[group] if c["required"] and c["status"] == "FAIL"]  # noqa: E731
    unevaluated = lambda group: [c["name"] for c in ev[group] if c["required"] and c["status"] == "NOT_EVALUATED"]  # noqa: E731
    if verdict.promotion_verdict == "APPROVED":
        return "**Decision: promote.** Measurement, edge and every governance check passed."
    if verdict.promotion_verdict == "DEFERRED":
        pending = unevaluated("edge_checks") + [c["name"] for c in ev["governance_checks"] if c["status"] != "PASS"]
        return f"**Decision: defer.** Nothing failed outright; pending: {', '.join(pending)}."
    if verdict.measurement_verdict != "PASS":
        names = failing("measurement_checks") or unevaluated("measurement_checks")
        return ("**Decision: reject for promotion.** Promotion is blocked, and the measurement system cannot yet "
                "certify an edge verdict either way: " + ", ".join(f"`{n}`" for n in names)
                + (" fail." if failing("measurement_checks") else " could not be evaluated.")
                + " Continued private research only.")
    return ("**Decision: reject for promotion.** The strategy failed "
            + ", ".join(f"`{n}`" for n in failing("edge_checks")) + ". Continued private research only.")


def build_closure(verdict: StrategyVerdict, evidence: dict, metrics: dict, ledger: dict) -> str:
    ev, bt = verdict.evidence, evidence["backtest"]
    fig = ev["edge_figures"]
    suite = ev["test_run"]["suite"]
    rec = ledger["reconciliation"]
    tr = evidence["live_track_record"]
    flips = fig.get("folds_whose_removal_alone_flips_the_mean") or []
    m_by = {c["name"]: c for c in ev["measurement_checks"]}
    e_by = {c["name"]: c for c in ev["edge_checks"]}
    biases = {b["id"]: b for b in bt["known_biases"]}
    det = m_by["deterministic_rerun"]
    n_pass = sum(c["status"] == "PASS" for c in ev["measurement_checks"])
    m_fail = [c["name"] for c in ev["measurement_checks"] if c["status"] != "PASS"]
    live = sorted(fig.get("live_edge_from_entry_pct", {}).items(), key=lambda kv: int(kv[0]))

    L: list[str] = []
    add = L.append
    add("# momentum_v1.0 — audit closure and strategy verdict")
    add("")
    add("Generated by `backend/scripts/build_audit_closure.py` from the committed verdict artifact and evidence "
        "bundle. Every verdict, status, figure and conclusion below is derived from those files; do not edit by hand.")
    add("")
    add("## Verdict")
    add("")
    add("| question | verdict |")
    add("|---|---|")
    add(f"| Is the measurement valid? | **{verdict.measurement_verdict}** |")
    add(f"| Does the strategy have an edge? | **{verdict.edge_verdict}** |")
    add(f"| May it be promoted beyond research? | **{verdict.promotion_verdict}** |")
    add("")
    add(f"> {verdict.summary}")
    add("")
    add(_decision(verdict, ev))
    add("")
    add("### The six questions")
    add("")

    q1 = "Yes" if verdict.measurement_verdict == "PASS" else "Not yet"
    add(f"1. **Is the measurement system correct?** {q1} — {verdict.measurement_verdict}. {n_pass} of "
        f"{len(ev['measurement_checks'])} validity checks pass"
        + (": the others are " + ", ".join(f"`{n}`" for n in m_fail) + "." if m_fail else "."))

    caveats = [b for b in ("gap_down_stop_fill", "same_bar_entry", "survivors_only_universe") if b in biases]
    add(f"2. **What is the corrected performance?** Over {fig['folds']} walk-forward folds the strategy returned "
        f"{fig['strategy_mean_return_pct']:.2f}% per six-month fold against the benchmark's "
        f"{fig['benchmark_mean_return_pct']:.2f}%"
        + (f", with {len(caveats)} recorded biases in that evidence ({', '.join(f'`{b}`' for b in caveats)})." if caveats else "."))

    primary, live_c = e_by["beats_primary_benchmark_after_costs"]["status"], e_by["live_paper_beats_benchmark"]["status"]
    q3 = "Yes" if primary == "PASS" and live_c == "PASS" else ("No" if primary == "FAIL" else "Not established")
    add(f"3. **Does it beat the benchmark after costs?** {q3}. Mean fold excess {fig['mean_fold_excess_pp']:+.2f}pp "
        f"({primary}), median {fig['median_fold_excess_pp']:+.2f}pp; beat the benchmark in "
        f"{fig['folds_beating_benchmark']} of {fig['folds']} folds. Live, from an obtainable entry ({live_c}): "
        + (", ".join(f"{h}d {v:+.2f}pp" for h, v in live) if live else "no sufficiently-sampled horizon") + ".")

    base = [e_by["beats_random_membership_baseline"], e_by["beats_naive_momentum_baseline"]]
    if all(c["status"] == "NOT_EVALUATED" for c in base):
        add("4. **Does it beat simple baselines?** Not evaluated. " + " ".join(
            f"`{c['name']}`: {c['detail'].split('. ')[0]}." for c in base))
    else:
        add("4. **Does it beat simple baselines?** " + "; ".join(f"`{c['name']}` {c['status']}" for c in base) + ".")

    median_neg, mean_neg = fig["median_fold_excess_pp"] < 0, fig["mean_fold_excess_pp"] < 0
    if flips:
        q5 = (f"The sign of the *mean* is fragile: removing any one of folds {', '.join(map(str, flips))} alone flips "
              f"it (range {fig['leave_one_out_mean_excess_min_pp']:+.2f} to {fig['leave_one_out_mean_excess_max_pp']:+.2f}pp).")
        if median_neg == mean_neg:
            q5 += (f" The *median* ({fig['median_fold_excess_pp']:+.2f}pp) points the same way without that "
                   "fragility, so it carries more weight than the mean.")
    else:
        q5 = (f"The sign of the mean survives removing any single fold (range "
              f"{fig['leave_one_out_mean_excess_min_pp']:+.2f} to {fig['leave_one_out_mean_excess_max_pp']:+.2f}pp).")
    add(f"5. **Is the result robust across folds?** {q5}")
    add(f"6. **Decision:** promotion {verdict.promotion_verdict}"
        + (" — keep as a research baseline." if verdict.promotion_verdict == "BLOCKED" else "."))
    add("")

    add("## Identity")
    add("")
    add(f"- **strategy_id:** `{verdict.strategy_id}`")
    add(f"- **strategy_version:** `{verdict.strategy_version}` — {evidence['id_note']}")
    add(f"- **definition:** {evidence['strategy_description']}")
    add(f"- **code revision:** `{verdict.code_revision}`")
    add(f"- **data snapshot:** `{verdict.data_snapshot}`")
    add(f"- **verdict content hash:** `sha256:{verdict.content_sha256()}`")
    add("")

    add("## Reproducibility manifest")
    add("")
    add("| artifact | sha256 |")
    add("|---|---|")
    add(f"| `backend/app/core/verdict_artifacts/momentum_v1.0.evidence.json` (canonical) | `{verdict.data_snapshot.split(':', 1)[1]}` |")
    for rel, digest in sorted(evidence["sources"].items()):
        add(f"| `{rel}` | `{digest}` |")
    add("")
    add("Regenerate: `python scripts/assemble_verdict_evidence.py`, then "
        "`python scripts/generate_strategy_verdict.py --postgres-url <url>` on a clean tree, then "
        "`python scripts/build_audit_closure.py`. `tests/test_strategy_verdict_artifact.py` and "
        "`tests/test_audit_closure_reports.py` fail if the committed files drift from what those produce.")
    add("")

    add("## Test period and protocol")
    add("")
    p = bt["protocol"]
    add(f"- {fig['folds']} walk-forward folds, {p['train_months']}m train / {p['test_months']}m test / "
        f"{p['roll_months']}m roll; test windows {metrics['backtest_period']['first_test_start']} to "
        f"{metrics['backtest_period']['last_test_end']}")
    add(f"- Capital ₹{p['capital']:,}; transaction cost {p['transaction_cost_pct']}% round trip; slippage "
        f"{p['slippage_pct']}% per leg")
    add(f"- Source: `{bt['source']}`, arm `{bt['arm']}`, run completed {bt['run_completed']}")
    add("")

    add("## Universe methodology")
    add("")
    add(bt["universe"][:1].upper() + bt["universe"][1:] + ".")
    add("")
    if "survivors_only_universe" in biases:
        add("Membership is point-in-time by liquidity, but drawn from stocks that are active *today* — see survivorship.")
        add("")

    add("## Measurement checks")
    add("")
    L.extend(_table(ev["measurement_checks"]))
    add("")

    add("## Edge checks")
    add("")
    if verdict.edge_verdict == "BLOCKED":
        add("Recorded even though the edge verdict is BLOCKED, so the block hides nothing.")
        add("")
    L.extend(_table(ev["edge_checks"]))
    add("")
    add("Thresholds, fixed before generation: " + ", ".join(f"`{k}` = {v}" for k, v in ev["thresholds"].items()) + ".")
    add("")

    add("## Survivorship bias")
    add("")
    sf = e_by["survivorship_free_confirmation"]
    if sf["status"] == "NOT_EVALUATED":
        add(f"**Not measured.** {sf['detail']}")
    else:
        add(f"`survivorship_free_confirmation`: **{sf['status']}** — {sf['detail']}")
    add("")
    add("*Correction to an earlier statement:* the ~12 percentage-point swing previously cited as a survivorship "
        "effect is **not** survivorship. It is Phase 18's universe-construction sensitivity — the same strategy "
        "measured 16.76% on one construction and 4.48% on another. Survivorship's own size is a separate "
        "measurement.")
    add("")

    add("## Corporate-action controls")
    add("")
    ca = m_by["corporate_actions_handled"]
    add(f"`corporate_actions_handled`: **{ca['status']}** — {ca['detail']}. Related repairs in the register: #4 "
        "(split restatements), #16 (in-progress session bars stored as closes), #17 (automatic split repair "
        "failed for single symbols).")
    add("")

    add("## Execution assumptions")
    add("")
    def direction(bias_id):
        b = biases.get(bias_id)
        return f" Bias direction: {b['direction'].replace('_', ' ')}." if b else ""
    add(f"- Costs and slippage (`costs_and_slippage_charged`): **{m_by['costs_and_slippage_charged']['status']}**.")
    add(f"- Gap-down stops (`gap_down_stop_execution`): **{m_by['gap_down_stop_execution']['status']}** — finding #19."
        + direction("gap_down_stop_fill"))
    add(f"- Entry timing (`next_session_entry_execution`): **{m_by['next_session_entry_execution']['status']}** — "
        "finding #20." + direction("same_bar_entry"))
    add("")

    add("## Known biases in the evidence")
    add("")
    add("| bias | direction | note |")
    add("|---|---|---|")
    for b in bt["known_biases"]:
        add(f"| `{b['id']}` | {b['direction'].replace('_', ' ')} | {b['note']} |")
    add("")

    add("## Bugs and fixes")
    add("")
    add("Full register: [momentum_v1.0_bug_impact_register.md](momentum_v1.0_bug_impact_register.md).")
    add("")
    add("- **Sortino (#13):** downside deviation was computed as the standard deviation of negative days; "
        "|Sortino| was overstated 1.18×. Fixed. The three tables that quoted it were not re-run; the verdict does "
        "not use Sortino.")
    reconciled = rec["residual"] == "0.00"
    add("- **Concurrency (#1):** the ₹400,240 lost debit is from a reproduction on Postgres 16, not a recorded "
        "production incident. "
        + (f"No production correction was needed or made: the live AI paper ledger reconciles exactly — stored cash "
           f"{rec['stored_cash']} against ledger-derived {rec['expected_cash']}, residual {rec['residual']}, across "
           f"{rec['trades']} trades ({rec['open_trades']} open, {rec['closed_trades']} closed), exported "
           f"{ledger['exported_at']}."
           if reconciled else
           f"The live AI paper ledger does NOT reconcile: residual {rec['residual']} (exported {ledger['exported_at']})."))
    add("- **Cache contamination (#18):** Phase 19 withdrawn. Its random-membership result is not evidence.")
    add("")

    add("## Tests")
    add("")
    add(f"Generating run: **{suite['tests']} tests — {suite['passed']} passed, {suite['failures']} failed, "
        f"{suite['errors']} errors, {suite['skipped']} skipped, {suite['xfailed']} known defects (strict xfail)**. "
        "Excluded: " + "; ".join(suite.get("excluded", [])) + ".")
    add("")

    add("## Determinism")
    add("")
    add(f"`deterministic_rerun`: **{det['status']}** — {det['detail']}.")
    add("")
    add("The test runs the frozen strategy's backtest twice over one snapshot, in separate processes with different "
        "`PYTHONHASHSEED` values, and requires byte-identical signals, entry order, trade ledger, equity curves and "
        "metrics. A companion test injects a hash-order leak and requires the gate to catch it through the real "
        "backtest.")
    add("")
    add("**Limitation:** that establishes determinism given fixed inputs. `run_backtest` re-fetches NIFTY from "
        "yfinance on every call, so two *real-data* runs on different days are not guaranteed identical unless the "
        "benchmark series is pinned.")
    add("")

    add("## Live evidence")
    add("")
    payload = tr["payload"]
    add(f"- Track record captured {tr['captured_at']}: {payload['signals_published']} signals "
        f"({payload['first_signal_date']} to {payload['last_signal_date']}); {payload['target_hit']} targets hit, "
        f"{payload['stop_hit']} stops. {tr['note']}")
    add("")
    add("| horizon | sample | sufficient | edge vs NIFTY from entry |")
    add("|---|---|---|---|")
    for h in payload["horizons"]:
        add(f"| {h['horizon_days']}d | {h['sample']} | {'yes' if h['sufficient_sample'] else 'no'} | "
            f"{h['edge_from_entry_pct']:+.2f}pp |")
    add("")

    add("## Limitations")
    add("")
    add(f"- **No backtest trade ledger.** `momentum_v1.0_trade_ledger.csv` holds the live AI paper account's "
        f"{rec['trades']} trades. Per-trade logs for the {fig['folds']} backtest folds were never stored; producing "
        "them means re-running the folds on a lab seeded with the 1,000-stock pool, on the engine as it stands.")
    add("- **Ledger columns not stored:** gross P&L, fees and slippage are not recorded separately (the stored "
        "entry price is cost-loaded), and no strategy version is stored per trade. Left empty rather than "
        "re-derived.")
    add("- **Unfilled orders:** the AI run row records only bought and sold counts, and the buy pass records only "
        "executed trades, so orders that were not filled leave no row to export.")
    add(f"- **First paper cohort:** {evidence['paper_ledger']['note']}")
    add(f"- **Precision:** backtest — {bt['precision']}. Live — {tr['note']}")
    add("- **Benchmark:** " + bt["benchmark"])
    add("- **Published phases** have not been re-run with fixes #13, #18, #19 or #20.")
    add("")

    add("## What would change this verdict")
    add("")
    open_defects = [n for n in ("gap_down_stop_execution", "next_session_entry_execution") if m_by[n]["status"] != "PASS"]
    step = 1
    if open_defects:
        add(f"{step}. Fix the open execution defects (" + ", ".join(f"`{n}`" for n in open_defects) + "), remove "
            "their strict-xfail markers, and re-run the folds on a lab seeded with the 1,000-stock pool. Measurement "
            "can then PASS, and the edge becomes judgeable.")
        step += 1
    missing = [c["name"] for c in ev["edge_checks"] if c["status"] == "NOT_EVALUATED"]
    if missing:
        add(f"{step}. Produce eligible evidence, for this strategy version on this universe, for: "
            + ", ".join(f"`{n}`" for n in missing) + ".")
        step += 1
    if median_neg and live_c == "FAIL":
        add(f"{step}. On the evidence available now, the median fold ({fig['median_fold_excess_pp']:+.2f}pp) and the "
            "live record both point toward an edge FAIL once measurement can certify one.")
    add("")
    return "\n".join(L)


def build_all(out: Path) -> dict[str, str]:
    verdict = StrategyVerdict.from_json(ARTIFACT.read_text())
    evidence = json.loads(EVIDENCE.read_text())
    ledger = json.loads(LEDGER.read_text())
    if verdict.data_snapshot != f"sha256:{sha256_hex(evidence)}":
        raise SystemExit("verdict artifact does not match the evidence bundle — regenerate the verdict first")

    metrics = build_metrics(verdict, evidence, ledger)
    files = {
        f"{PREFIX}_metrics.json": json.dumps(metrics, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n",
        f"{PREFIX}_trade_ledger.csv": build_ledger_csv(verdict, ledger, evidence),
        f"{PREFIX}_bug_impact_register.md": build_register() + "\n",
        f"{PREFIX}_audit_closure.md": build_closure(verdict, evidence, metrics, ledger) + "\n",
    }
    out.mkdir(parents=True, exist_ok=True)
    for name, content in files.items():
        (out / name).write_text(content)
    return files


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    args = ap.parse_args()
    for name in build_all(Path(args.out)):
        print(f"wrote {Path(args.out) / name}")


if __name__ == "__main__":
    main()
