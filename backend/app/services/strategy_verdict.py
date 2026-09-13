"""Compute a strategy verdict from recorded test outcomes and committed evidence.

Pure: compute_verdict() takes the test outcomes and the evidence bundle as data
and returns a StrategyVerdict. It runs no tests, reads no files, calls no
network. scripts/generate_strategy_verdict.py gathers the inputs;
tests/test_strategy_verdict_artifact.py re-runs this on the committed inputs and
requires the committed artifact to match, so the artifact cannot be edited by
hand and cannot drift from the logic that produced it.

Three groups of checks, each with an explicit source:

MEASUREMENT — every check names the tests it rests on. A check is PASS only if
each named test ran and passed. A strict xfail is a KNOWN DEFECT and makes the
check FAIL. A skip, or a named test missing from the run, makes it
NOT_EVALUATED — never an assumed PASS.

EDGE — computed from the committed backtest folds and the live track record,
against the thresholds below. Evidence that does not match the strategy version
and universe under test is ineligible and recorded as such; a check with no
eligible evidence is NOT_EVALUATED rather than borrowed from a different
experiment.

GOVERNANCE — explicit attestations in the evidence bundle. A backtest cannot
settle data terms, scope, or regulatory review.
"""

from dataclasses import asdict
from statistics import mean, median

from app.core.verdict import (
    Check,
    StrategyVerdict,
    build_summary,
    decide_edge,
    decide_measurement,
    decide_promotion,
)

# --- edge thresholds ------------------------------------------------------------
# Fixed 2026-09-13, before this verdict was generated. Deliberately plain: an
# edge means beating the index after costs, not "positive". None of them could
# have been tuned toward the result — the strategy's mean and median fold excess
# are both negative, which fails the first two regardless of where the rest sit.
EDGE_MIN_MEAN_FOLD_EXCESS_PP = 0.0      # mean over folds of (strategy - benchmark) return
EDGE_MIN_MEDIAN_FOLD_EXCESS_PP = 0.0    # the typical fold, so one great fold cannot carry it
# Max drawdown may be worse than the benchmark's by at most this much, averaged
# over folds. 5pp: a momentum book is expected to be somewhat more volatile than
# the index, but not to buy its excess return with materially deeper losses.
EDGE_DRAWDOWN_TOLERANCE_PP = 5.0
# Live: the edge measured from an obtainable entry (audit finding #7), required
# positive at every horizon with a sufficient sample.
LIVE_MIN_EDGE_FROM_ENTRY_PCT = 0.0

# --- measurement checks -----------------------------------------------------------
# name -> (tests the check rests on, what PASS means). Node ids are
# file::function; parametrized cases are aggregated under the function name.
MEASUREMENT_SPECS: tuple[tuple[str, tuple[str, ...], str], ...] = (
    ("no_lookahead_in_scoring", (
        "tests/test_backtest_execution.py::test_scoring_and_entries_do_not_see_bars_after_the_rebalance_date",
    ), "signals and entries before a date are unaffected by bars after it"),
    ("costs_and_slippage_charged", (
        "tests/test_backtest_execution.py::test_costs_and_slippage_are_charged_on_both_legs_exactly",
    ), "transaction costs and slippage are charged on both legs, exactly"),
    ("gap_down_stop_execution", (
        "tests/test_backtest_execution.py::test_a_gap_down_through_the_stop_fills_at_the_open_not_the_stop",
    ), "a gap through the stop fills at or below the open"),
    ("next_session_entry_execution", (
        "tests/test_backtest_execution.py::test_entries_fill_in_the_session_after_the_signal",
    ), "entries fill in the session after the signal, not on the signal bar"),
    ("survivorship_handling", (
        "tests/test_backtest_survivorship.py::test_the_default_universe_is_survivors_only",
        "tests/test_backtest_survivorship.py::test_include_inactive_lets_a_delisted_name_into_the_universe",
        "tests/test_backtest_survivorship.py::test_a_position_in_a_delisted_name_is_closed_not_carried",
        "tests/test_backtest_survivorship.py::test_a_series_that_stops_forces_an_exit",
        "tests/test_backtest_survivorship.py::test_a_delisted_exit_is_labelled_not_hidden",
    ), "delisted names can be included, and a dead series is exited rather than carried"),
    ("corporate_actions_handled", (
        "tests/test_corporate_actions.py::test_a_two_for_one_split_is_flagged",
        "tests/test_corporate_actions.py::test_a_restated_symbol_is_repulled_and_not_appended_to",
        "tests/test_corporate_actions.py::test_rounding_noise_is_not_a_restatement",
        "tests/test_price_integrity.py::test_a_split_stock_gets_no_momentum_score",
        "tests/test_incremental.py::test_restatement_repull_fetches_max_history",
        "tests/test_incremental.py::test_in_progress_bar_is_dropped_mid_session",
        "tests/test_ticker_frame.py::test_full_batch_of_one_symbol_returns_it",
    ), "splits are detected, restated history re-pulled in full, unsettled bars refused"),
    ("indicator_cache_universe_safe", (
        "tests/test_indicator_cache.py::test_a_universe_scores_the_same_after_a_different_universe_filled_the_cache",
        "tests/test_indicator_cache.py::test_a_narrower_universe_is_not_contaminated_by_a_wider_cache_entry",
    ), "a shared indicator cache cannot change what a universe scores as"),
    ("metrics_validated", (
        "tests/test_backtest_metrics.py::test_sharpe_matches_the_definition",
        "tests/test_backtest_metrics.py::test_sortino_uses_downside_deviation_not_the_std_of_negative_days",
        "tests/test_backtest_metrics.py::test_cagr_is_annualised_over_the_calendar_span",
        "tests/test_backtest_metrics.py::test_max_drawdown_is_measured_from_the_running_peak",
        "tests/test_backtest_metrics.py::test_drawdown_duration_counts_trading_sessions_under_water",
        "tests/test_backtest_metrics.py::test_calmar_is_cagr_over_the_drawdown_and_none_without_one",
    ), "reported metrics match their definitions"),
    ("deterministic_rerun", (
        "tests/test_determinism.py::test_same_snapshot_produces_identical_outputs",
        "tests/test_determinism.py::test_the_seeds_actually_disagree_on_the_tie",
        "tests/test_determinism.py::test_the_gate_catches_an_injected_iteration_order_leak",
    ), "the same snapshot gives byte-identical output across processes, and the gate is proven to catch a leak"),
    ("paper_ledger_identity", (
        "tests/test_money_ledger.py::test_identity_holds_exactly_over_many_round_trips",
        "tests/test_money_ledger.py::test_cash_ties_out_to_the_sum_of_cash_movements",
        "tests/test_money_ledger.py::test_randomised_round_trips_match_an_exact_shadow_ledger",
        "tests/test_money_ledger.py::test_every_money_field_is_decimal",
    ), "the paper ledger's money identity holds exactly, in Decimal"),
    ("concurrent_portfolio_updates", (
        "tests/test_concurrency_and_atomicity.py::test_concurrent_buys_do_not_lose_a_debit",
        "tests/test_concurrency_and_atomicity.py::test_the_locked_read_refreshes_a_stale_attribute",
        "tests/test_concurrency_and_atomicity.py::test_the_schema_forbids_two_open_positions_in_one_stock",
        "tests/test_concurrency_and_atomicity.py::test_a_cycle_that_raises_halfway_commits_no_trades",
    ), "concurrent buys cannot lose a debit, and a failed cycle commits nothing"),
)


def referenced_tests() -> list[str]:
    return [node for _, nodes, _ in MEASUREMENT_SPECS for node in nodes]


def _test_check(name: str, nodes: tuple[str, ...], meaning: str, outcomes: dict) -> Check:
    missing = [n for n in nodes if n not in outcomes]
    failed = [n for n in nodes if outcomes.get(n, {}).get("status") == "failed"]
    xfailed = [n for n in nodes if outcomes.get(n, {}).get("status") == "xfailed"]
    skipped = [n for n in nodes if outcomes.get(n, {}).get("status") == "skipped"]
    short = lambda n: n.split("::")[-1]  # noqa: E731

    if failed:
        status, detail = "FAIL", f"test failed: {', '.join(map(short, failed))}"
    elif xfailed:
        reasons = "; ".join(outcomes[n].get("message", "") for n in xfailed)
        status, detail = "FAIL", f"known defect (strict xfail): {reasons}"
    elif missing:
        status, detail = "NOT_EVALUATED", f"test not present in the run: {', '.join(map(short, missing))}"
    elif skipped:
        reasons = "; ".join(f"{short(n)}: {outcomes[n].get('message', '')}" for n in skipped)
        status, detail = "NOT_EVALUATED", f"test skipped, so not evidence: {reasons}"
    else:
        status, detail = "PASS", f"{len(nodes)} test(s) passed: {meaning}"
    return Check(name=name, status=status, required=True, detail=detail, evidence=nodes)


def measurement_checks(outcomes: dict, suite: dict, evidence: dict) -> list[Check]:
    checks = [_test_check(name, nodes, meaning, outcomes) for name, nodes, meaning in MEASUREMENT_SPECS]

    bad = suite.get("failures", 0) + suite.get("errors", 0)
    checks.append(Check(
        name="regression_suite_green",
        status="FAIL" if bad else ("PASS" if suite.get("tests", 0) > 0 else "NOT_EVALUATED"),
        required=True,
        detail=(f"{suite.get('tests', 0)} tests: {suite.get('passed', 0)} passed, {suite.get('failures', 0)} failed, "
                f"{suite.get('errors', 0)} errors, {suite.get('skipped', 0)} skipped, "
                f"{suite.get('xfailed', 0)} known defects (xfail)"),
        evidence=("test_run.suite",),
    ))

    rec = (evidence.get("paper_ledger") or {}).get("reconciliation")
    if not rec:
        status, detail = "NOT_EVALUATED", "no live paper ledger export in the evidence bundle"
    elif rec.get("residual") == "0.00":
        status, detail = "PASS", (
            f"live AI paper account: {rec['trades']} trades reconcile exactly — stored cash "
            f"{rec['stored_cash']} == {rec['identity'].split('==')[1].strip()} = {rec['expected_cash']}"
        )
    else:
        status, detail = "FAIL", f"live AI paper account does not reconcile: residual {rec.get('residual')}"
    checks.append(Check(name="live_paper_ledger_reconciles", status=status, required=True, detail=detail,
                        evidence=("paper_ledger.reconciliation",)))
    return checks


COMPARISON_CHECKS = ("beats_random_membership_baseline", "beats_naive_momentum_baseline",
                     "passes_stress_slippage", "not_dependent_on_single_sector",
                     "survivorship_free_confirmation")
_COMPARISONS = {
    ">": lambda a, b: a > b,
    ">=": lambda a, b: a >= b,
    "<": lambda a, b: a < b,
    "<=": lambda a, b: a <= b,
}


def _fmt(x: float) -> str:
    return f"{x:+.2f}"


def edge_checks(evidence: dict) -> tuple[list[Check], dict]:
    bt = evidence["backtest"]
    folds = bt["folds"]
    strat = [f["strategy"]["total_return_pct"] for f in folds]
    bench = [f["benchmark"]["total_return_pct"] for f in folds]
    excess = [s - b for s, b in zip(strat, bench)]
    n = len(excess)
    src = (bt["source"],)
    checks: list[Check] = []
    figures: dict = {"folds": n}

    if n == 0:
        for name in ("beats_primary_benchmark_after_costs", "positive_median_fold_excess_return",
                     "not_dependent_on_single_fold", "drawdown_within_tolerance"):
            checks.append(Check(name, "NOT_EVALUATED", True, "no backtest folds in the evidence bundle", src))
    else:
        figures.update({
            "strategy_mean_return_pct": round(mean(strat), 4),
            "benchmark_mean_return_pct": round(mean(bench), 4),
            "mean_fold_excess_pp": round(mean(excess), 4),
            "median_fold_excess_pp": round(median(excess), 4),
            "folds_beating_benchmark": sum(e > 0 for e in excess),
            "strategy_mean_max_drawdown_pct": round(mean(f["strategy"]["max_drawdown_pct"] for f in folds), 4),
            "benchmark_mean_max_drawdown_pct": round(mean(f["benchmark"]["max_drawdown_pct"] for f in folds), 4),
        })

        ok = figures["mean_fold_excess_pp"] > EDGE_MIN_MEAN_FOLD_EXCESS_PP
        checks.append(Check("beats_primary_benchmark_after_costs", "PASS" if ok else "FAIL", True,
                            f"mean fold excess {_fmt(figures['mean_fold_excess_pp'])}pp over {n} folds "
                            f"(strategy {figures['strategy_mean_return_pct']:.2f}% vs benchmark "
                            f"{figures['benchmark_mean_return_pct']:.2f}%); threshold > "
                            f"{EDGE_MIN_MEAN_FOLD_EXCESS_PP}pp", src))

        ok = figures["median_fold_excess_pp"] > EDGE_MIN_MEDIAN_FOLD_EXCESS_PP
        checks.append(Check("positive_median_fold_excess_return", "PASS" if ok else "FAIL", True,
                            f"median fold excess {_fmt(figures['median_fold_excess_pp'])}pp; beat the benchmark "
                            f"in {figures['folds_beating_benchmark']} of {n} folds; threshold > "
                            f"{EDGE_MIN_MEDIAN_FOLD_EXCESS_PP}pp", src))

        if n < 2:
            # Leave-one-out needs something left over.
            checks.append(Check("not_dependent_on_single_fold", "NOT_EVALUATED", True,
                                f"only {n} fold — dependence on a single fold cannot be measured", src))
        else:
            loo = [mean(excess[:i] + excess[i + 1:]) for i in range(n)]
            worst = min(range(n), key=lambda i: loo[i])
            best = max(range(n), key=lambda i: loo[i])
            figures["leave_one_out_mean_excess_min_pp"] = round(loo[worst], 4)
            figures["leave_one_out_mean_excess_max_pp"] = round(loo[best], 4)
            # Which fold moves the mean most when removed — named, because a
            # conclusion that one fold decides is weaker than it looks either way.
            figures["fold_whose_removal_raises_mean_most"] = folds[best].get("fold", best + 1)
            # Every fold whose removal ALONE flips the sign of the mean. One such
            # fold means the headline sign is fragile; several mean it is decided
            # by whichever outlier happens to be in the window.
            full = mean(excess)
            flips = [folds[i].get("fold", i + 1) for i in range(n) if (loo[i] > 0) != (full > 0)]
            figures["folds_whose_removal_alone_flips_the_mean"] = flips
            ok = loo[worst] > 0
            checks.append(Check("not_dependent_on_single_fold", "PASS" if ok else "FAIL", True,
                                f"dropping any one fold leaves mean excess between {_fmt(loo[worst])} and "
                                f"{_fmt(loo[best])}pp; removing fold(s) {flips or 'none'} alone flips the sign of "
                                f"the {_fmt(full)}pp mean; PASS requires it positive after dropping every "
                                f"fold in turn", src))

        dd_gap = figures["strategy_mean_max_drawdown_pct"] - figures["benchmark_mean_max_drawdown_pct"]
        ok = dd_gap >= -EDGE_DRAWDOWN_TOLERANCE_PP
        checks.append(Check("drawdown_within_tolerance", "PASS" if ok else "FAIL", True,
                            f"mean fold max drawdown {figures['strategy_mean_max_drawdown_pct']:.2f}% vs benchmark "
                            f"{figures['benchmark_mean_max_drawdown_pct']:.2f}% ({_fmt(dd_gap)}pp); tolerance "
                            f"{EDGE_DRAWDOWN_TOLERANCE_PP}pp worse", src))

    # Checks that need an experiment of their own. Each becomes evaluable the
    # moment eligible evidence for it is added to the bundle as
    # {check, source, measured, threshold, comparison, detail}; until then it is
    # NOT_EVALUATED with the recorded reason. Without this path an edge PASS
    # would be structurally unreachable, which is not the same thing as honest.
    eligible = {e["check"]: e for e in evidence.get("eligible_evidence", [])}
    ineligible = {e["check"]: e for e in evidence.get("ineligible_evidence", [])}
    for name in COMPARISON_CHECKS:
        if name in eligible:
            e = eligible[name]
            op = _COMPARISONS[e["comparison"]]
            ok = op(e["measured"], e["threshold"])
            checks.append(Check(name, "PASS" if ok else "FAIL", True,
                                f"{e['detail']}: measured {e['measured']}, required {e['comparison']} {e['threshold']}",
                                (e["source"],)))
        else:
            e = ineligible.get(name)
            checks.append(Check(name, "NOT_EVALUATED", True,
                                e["reason"] if e else "no eligible evidence recorded",
                                (e["source"],) if e and e.get("source") else ()))

    tr = (evidence.get("live_track_record") or {}).get("payload") or {}
    horizons = [h for h in tr.get("horizons", []) if h.get("sufficient_sample")]
    if not horizons:
        checks.append(Check("live_paper_beats_benchmark", "NOT_EVALUATED", True,
                            "no live horizon has a sufficient sample", ("live_track_record",)))
    else:
        parts = [f"{h['horizon_days']}d {_fmt(h['edge_from_entry_pct'])}pp (n={h['sample']})" for h in horizons]
        ok = all(h["edge_from_entry_pct"] > LIVE_MIN_EDGE_FROM_ENTRY_PCT for h in horizons)
        checks.append(Check("live_paper_beats_benchmark", "PASS" if ok else "FAIL", True,
                            f"{tr.get('signals_published')} live signals; edge vs NIFTY from an obtainable entry: "
                            f"{', '.join(parts)}; PASS requires every sufficiently-sampled horizon > "
                            f"{LIVE_MIN_EDGE_FROM_ENTRY_PCT}pp", ("live_track_record",)))
        figures["live_edge_from_entry_pct"] = {str(h["horizon_days"]): h["edge_from_entry_pct"] for h in horizons}
    return checks, figures


def governance_checks(evidence: dict) -> list[Check]:
    out = []
    for name, g in sorted((evidence.get("governance") or {}).items()):
        out.append(Check(name, g["status"], True, g["detail"], ("governance",)))
    return out


def compute_verdict(*, evidence: dict, evidence_sha256: str, outcomes: dict, suite: dict,
                    code_revision: str, generated_at: str) -> StrategyVerdict:
    m_checks = measurement_checks(outcomes, suite, evidence)
    e_checks, figures = edge_checks(evidence)
    g_checks = governance_checks(evidence)

    measurement = decide_measurement(m_checks)
    edge = decide_edge(e_checks, measurement)
    promotion = decide_promotion(measurement, edge, g_checks)

    return StrategyVerdict(
        strategy_id=evidence["strategy_id"],
        strategy_version=evidence["strategy_version"],
        measurement_verdict=measurement,
        edge_verdict=edge,
        promotion_verdict=promotion,
        generated_at=generated_at,
        code_revision=code_revision,
        data_snapshot=f"sha256:{evidence_sha256}",
        summary=build_summary(measurement, edge, promotion, m_checks, e_checks, g_checks),
        evidence={
            "measurement_checks": [asdict(c) for c in m_checks],
            "edge_checks": [asdict(c) for c in e_checks],
            "governance_checks": [asdict(c) for c in g_checks],
            "edge_figures": figures,
            "thresholds": {
                "edge_min_mean_fold_excess_pp": EDGE_MIN_MEAN_FOLD_EXCESS_PP,
                "edge_min_median_fold_excess_pp": EDGE_MIN_MEDIAN_FOLD_EXCESS_PP,
                "edge_drawdown_tolerance_pp": EDGE_DRAWDOWN_TOLERANCE_PP,
                "live_min_edge_from_entry_pct": LIVE_MIN_EDGE_FROM_ENTRY_PCT,
            },
            "known_biases": evidence["backtest"].get("known_biases", []),
            "benchmark": evidence["backtest"]["benchmark"],
            "universe": evidence["backtest"]["universe"],
            "live_signals_through": (evidence.get("live_track_record") or {}).get("payload", {}).get("last_signal_date"),
            "test_run": {"suite": suite, "outcomes": {k: outcomes[k] for k in sorted(outcomes) if k in set(referenced_tests())}},
        },
    )
