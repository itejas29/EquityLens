"""Phase 28 — event backtest of the pre-registered alternative-data signals.

Rules: docs/experiments/phase28_alternative_data/PREREGISTRATION.md (committed
before any of this data was loaded). Each signal is turned into trades
exactly as registered:

* entry at the open of the first session after the public date;
* hold 60 sessions, exit at the next open; stop at 85% of entry;
* 0.24% round trip on stocks, 0.10% on the index;
* NIFTY 50 total return (price + 1.3% a year) over the same window as the
  benchmark;
* survivorship-free universe B of the Phase 24 fold containing the signal.

Returns are ratios, not money, so they are floats; the rupee thresholds the
signals use are compared as Decimal against the NUMERIC columns.

Lab only.  DATABASE_URL=<lab> python scripts/phase28_event_backtest.py
"""

import bisect
import json
import logging
import os
import sys
import zipfile
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from statistics import mean, median

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np  # noqa: E402
from scipy import stats  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.core.database import SessionLocal  # noqa: E402
from app.core.experiment_paths import experiment_dir  # noqa: E402
from app.core.lab_guard import require_lab_database  # noqa: E402
from app.core.universe_config import HISTORY_PERIOD  # noqa: E402
from app.services.market_data import fetch_price_history  # noqa: E402
from phase28_http import ROOT  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("p28")

OUT = experiment_dir("phase28_alternative_data")
P24 = experiment_dir("phase24_survivorship")
BHAV = Path(os.environ.get("NSE_ARCHIVE_DIR", Path.home() / ".cache/equitylens/nse")).expanduser() / "bhavcopy"

SAMPLE_START = date(2018, 4, 1)
HOLD_SESSIONS = 60
STOP_FRACTION = 0.85
STOCK_COST_RT = 0.0024
INDEX_COST_RT = 0.0010
DIVIDEND_YIELD = 0.013          # NIFTY 50 total return ≈ price + 1.3% a year
DELIST_GAP_DAYS = 30
ALPHA = 0.10 / 5                # Bonferroni over the five registered signals
MIN_TRADES = 30
S1_MIN_PERSONS, S1_MIN_VALUE, S1_WINDOW = 2, Decimal("5000000"), 5      # ₹50 lakh
S2_MIN_BUY_VALUE = Decimal("100000000")                                # ₹10 crore
S4_THRESHOLD_CR = Decimal("-5000")


# ------------------------------------------------------------------ data --

class Bars:
    """One instrument's daily bars as parallel arrays, sorted by date."""

    def __init__(self, dates, o, h, lo, c):
        self.d, self.o, self.h, self.lo, self.c = dates, o, h, lo, c

    def first_after(self, day: date) -> int | None:
        i = bisect.bisect_right(self.d, day)
        return i if i < len(self.d) else None

    def first_on_or_after(self, day: date) -> int | None:
        i = bisect.bisect_left(self.d, day)
        return i if i < len(self.d) else None


def load_nifty() -> tuple[Bars, list[date]]:
    df = fetch_price_history("^NSEI", period=HISTORY_PERIOD).dropna(subset=["open", "close"]).sort_values("date")
    (ROOT / "nifty_pinned.json").write_text(df[["date", "open", "high", "low", "close"]].to_json(
        orient="records", date_format="iso"))
    dates = [d if isinstance(d, date) else d.date() for d in df["date"]]
    bars = Bars(dates, *(df[k].astype(float).tolist() for k in ("open", "high", "low", "close")))
    return bars, dates


def load_stock_bars(db, ids: set[int]) -> dict[int, Bars]:
    rows = db.execute(text("""SELECT stock_id, date, open, high, low, close FROM price_history
        WHERE stock_id = ANY(:ids) AND open IS NOT NULL AND low IS NOT NULL AND close IS NOT NULL
        ORDER BY stock_id, date"""), {"ids": list(ids)}).all()
    out, cur, buf = {}, None, []
    for r in rows + [None]:
        if r is None or r.stock_id != cur:
            if buf:
                out[cur] = Bars([b[0] for b in buf], *([float(b[k]) for b in buf] for k in range(1, 5)))
            if r is None:
                break
            cur, buf = r.stock_id, []
        buf.append((r.date, r.open, r.high, r.low, r.close))
    return out


def bhav_isin_intervals() -> dict[str, list[tuple[date, date, str]]]:
    """symbol -> [(first_date, last_date, isin)], from NSE's own bhavcopies.
    Cached, because reading 2,500 zips takes minutes."""
    cache = ROOT / "bhav_symbol_isin.json"
    if cache.exists():
        raw = json.loads(cache.read_text())
        return {s: [(date.fromisoformat(a), date.fromisoformat(b), i) for a, b, i in v] for s, v in raw.items()}
    span: dict[tuple[str, str], list[date]] = {}
    for p in sorted(BHAV.glob("*.zip")):
        day = date(int(p.stem[:4]), int(p.stem[4:6]), int(p.stem[6:8]))
        with zipfile.ZipFile(p) as z:
            for name in z.namelist():
                lines = z.read(name).decode("latin-1").splitlines()
                cols = [c.strip().upper() for c in lines[0].split(",")]
                si = cols.index("SYMBOL") if "SYMBOL" in cols else cols.index("TCKRSYMB")
                se = cols.index("SERIES") if "SERIES" in cols else cols.index("SCTYSRS")
                ii = cols.index("ISIN")
                for line in lines[1:]:
                    f = line.split(",")
                    if len(f) > max(si, se, ii) and f[se].strip() == "EQ":
                        key = (f[si].strip(), f[ii].strip())
                        s = span.setdefault(key, [day, day])
                        s[0], s[1] = min(s[0], day), max(s[1], day)
    out: dict[str, list] = defaultdict(list)
    for (sym, isin), (a, b) in span.items():
        out[sym].append((a, b, isin))
    for v in out.values():
        v.sort()
    cache.write_text(json.dumps({s: [(a.isoformat(), b.isoformat(), i) for a, b, i in v] for s, v in out.items()}))
    return dict(out)


class Mapper:
    """NSE symbol on a date -> lab stock id, through the ISIN (renames keep it)."""

    def __init__(self, db, intervals):
        self.intervals = intervals
        self.by_symbol = {r.symbol: r.id for r in db.execute(text("SELECT id, symbol FROM stocks")).all()}
        self.by_isin: dict[str, int] = {}
        catalogue = dict(db.execute(text("SELECT symbol, isin FROM nse_symbols WHERE isin IS NOT NULL")).all())
        for sym, sid in self.by_symbol.items():
            base = sym[3:] if sym.startswith("DL:") else sym
            isins = {i for _, _, i in intervals.get(base, [])}
            if not sym.startswith("DL:") and catalogue.get(sym):
                isins.add(catalogue[sym])
            for i in isins:
                # A listed name wins over a DL: record sharing its ISIN.
                if i not in self.by_isin or not sym.startswith("DL:"):
                    self.by_isin[i] = sid
        self.stats = defaultdict(int)

    def __call__(self, symbol: str, day: date) -> int | None:
        iv = self.intervals.get(symbol, [])
        isin = next((i for a, b, i in iv if a <= day <= b), None) or next(
            (i for a, b, i in reversed(iv) if a <= day), None) or (iv[-1][2] if iv else None)
        if isin and isin in self.by_isin:
            self.stats["isin"] += 1
            return self.by_isin[isin]
        for s in (symbol, "DL:" + symbol):
            if s in self.by_symbol:
                self.stats["symbol"] += 1
                return self.by_symbol[s]
        self.stats["unmatched"] += 1
        return None


class Universe:
    def __init__(self):
        folds = json.loads((P24 / "universes.json").read_text())["folds"]
        self.folds = [(date.fromisoformat(f["test"][0]), date.fromisoformat(f["test"][1]), set(f["B"]))
                      for f in folds]
        self.all_ids = set().union(*(b for _, _, b in self.folds))

    def contains(self, sid: int, day: date) -> bool:
        if day < SAMPLE_START:
            return False
        for a, b, ids in self.folds:
            if a <= day <= b:
                return sid in ids
        # Between folds (a fold ends on a Sunday, the next starts the day
        # after) cannot happen; after the last fold, fold 16 applies.
        return sid in self.folds[-1][2] if day > self.folds[-1][1] else False


# ---------------------------------------------------------------- trades --

def simulate(bars: Bars, public: date, cal: list[date], nifty: Bars, cost_rt: float,
             add_dividend: bool) -> dict | None:
    """One registered trade, or None if it cannot be entered or cannot finish."""
    e = bars.first_after(public)
    if e is None or (bars.d[e] - public).days > DELIST_GAP_DAYS:
        return None
    entry_day, entry_px = bars.d[e], bars.o[e]
    ci = bisect.bisect_left(cal, entry_day)
    if ci + HOLD_SESSIONS >= len(cal):
        return None                                  # cannot finish inside the data
    exit_target = cal[ci + HOLD_SESSIONS]
    stop = entry_px * STOP_FRACTION
    exit_day = exit_px = None
    reason = "time"
    i = e
    while i < len(bars.d) and bars.d[i] < exit_target:
        if i > e and (bars.d[i] - bars.d[i - 1]).days > DELIST_GAP_DAYS:
            exit_day, exit_px, reason = bars.d[i - 1], bars.c[i - 1], "delisted"
            break
        if bars.lo[i] <= stop:
            exit_day, exit_px, reason = bars.d[i], min(bars.o[i], stop) if i > e else stop, "stop"
            break
        i += 1
    if exit_day is None:
        j = bars.first_on_or_after(exit_target)
        if j is None or (bars.d[j] - exit_target).days > DELIST_GAP_DAYS:
            last = bars.d[-1]
            if (cal[-1] - last).days <= DELIST_GAP_DAYS:
                return None                          # data simply ends: cannot finish
            exit_day, exit_px, reason = last, bars.c[-1], "delisted"
        else:
            exit_day, exit_px = bars.d[j], bars.o[j]
    gross = exit_px / entry_px - 1
    net = (exit_px * (1 - cost_rt / 2)) / (entry_px * (1 + cost_rt / 2)) - 1
    days = (exit_day - entry_day).days
    div = DIVIDEND_YIELD * days / 365
    if add_dividend:                                 # the index trade is an ETF: it earns the dividends
        net = (1 + net) * (1 + div) - 1
    ne = nifty.first_on_or_after(entry_day)
    nx = nifty.first_on_or_after(exit_day)
    if ne is None or nx is None:
        return None
    n_exit = nifty.c[nx] if reason == "delisted" else nifty.o[nx]
    bench = (n_exit / nifty.o[ne]) * (1 + div) - 1
    return {"entry": entry_day.isoformat(), "exit": exit_day.isoformat(), "reason": reason, "days": days,
            "gross": gross, "net": net, "bench": bench, "excess": net - bench}


def run_stock_signal(name, signals, mapper, universe, bars_by_id, cal, nifty, cost_rt=STOCK_COST_RT):
    """signals: [(public_date, symbol, detail)] in time order."""
    counts = defaultdict(int)
    trades, open_until = [], {}
    for public, symbol, detail in sorted(signals, key=lambda s: s[0]):
        counts["signals"] += 1
        if public < SAMPLE_START:
            counts["before_sample"] += 1
            continue
        sid = mapper(symbol, public)
        if sid is None:
            counts["unmapped"] += 1
            continue
        if not universe.contains(sid, public):
            counts["outside_universe"] += 1
            continue
        if sid in open_until and public < open_until[sid]:
            counts["already_held"] += 1
            continue
        bars = bars_by_id.get(sid)
        t = simulate(bars, public, cal, nifty, cost_rt, add_dividend=False) if bars else None
        if t is None:
            counts["cannot_enter_or_finish"] += 1
            continue
        open_until[sid] = date.fromisoformat(t["exit"])
        trades.append({"public": public.isoformat(), "symbol": symbol, "stock_id": sid, **detail, **t})
    log.info("%s: %s -> %d trades", name, dict(counts), len(trades))
    return trades, dict(counts)


def evaluate(trades: list[dict]) -> dict:
    x = [t["excess"] for t in trades]
    out = {"trades": len(x)}
    if not x:
        return {**out, "verdict": "INSUFFICIENT"}
    out.update({"median_excess_pct": median(x) * 100, "mean_excess_pct": mean(x) * 100,
                "win_rate_pct": sum(v > 0 for v in x) / len(x) * 100,
                "median_net_pct": median(t["net"] for t in trades) * 100,
                "median_bench_pct": median(t["bench"] for t in trades) * 100,
                "exit_reasons": {r: sum(t["reason"] == r for t in trades) for r in ("time", "stop", "delisted")}})
    out["p_value"] = float(stats.ttest_1samp(x, 0.0, alternative="greater").pvalue) if len(x) > 1 else None
    if len(x) < MIN_TRADES:
        out["verdict"] = "INSUFFICIENT"
    else:
        ok = out["median_excess_pct"] > 0 and out["p_value"] is not None and out["p_value"] < ALPHA
        out["verdict"] = "PASS" if ok else "FAIL"
    return out


def robustness(trades: list[dict], rerun_at_cost) -> dict:
    by_date = sorted(trades, key=lambda t: t["public"])
    cut = by_date[len(by_date) // 2]["public"]
    halves = [[t for t in by_date if t["public"] < cut], [t for t in by_date if t["public"] >= cut]]
    monthly = defaultdict(list)
    for t in trades:
        monthly[t["entry"][:7]].append(t["excess"])
    means = [mean(v) for v in monthly.values()]
    p_month = float(stats.ttest_1samp(means, 0.0, alternative="greater").pvalue) if len(means) > 1 else None
    high_cost = evaluate(rerun_at_cost(0.0035))
    res = {"split_date": cut, "halves_median_excess_pct": [median(t["excess"] for t in h) * 100 if h else None
                                                           for h in halves],
           "cost_035": high_cost, "months": len(means), "monthly_clustered_p": p_month}
    res["robust"] = (all(v is not None and v > 0 for v in res["halves_median_excess_pct"])
                     and high_cost["verdict"] == "PASS" and p_month is not None and p_month < ALPHA)
    return res


# --------------------------------------------------------------- signals --

def s1_signals(db, cal) -> tuple[list, dict]:
    rows = db.execute(text("""SELECT symbol, person, category, mode, txn_type, sec_type, quantity, value_inr,
        acq_from, acq_to, broadcast_at FROM altdata.insider_pit ORDER BY broadcast_at""")).all()
    info = {"records": len(rows)}
    seen, recs = set(), []
    for r in rows:
        if r.broadcast_at is None:
            continue
        cat, mode = (r.category or "").lower(), (r.mode or "").strip().lower()
        if (r.txn_type or "").strip().lower() != "buy" or mode != "market purchase":
            continue
        if "equity" not in (r.sec_type or "").lower():
            continue
        if not ("promoter" in cat or "director" in cat):
            continue
        if r.value_inr is None or r.value_inr <= 0:
            continue
        key = (r.symbol, r.person.upper(), r.acq_from, r.acq_to, r.quantity, r.value_inr)
        if key in seen:
            continue                                  # a re-filed disclosure; the earliest broadcast stands
        seen.add(key)
        recs.append((r.broadcast_at.date(), r.symbol, r.person.upper(), r.value_inr))
    info["qualifying_records"] = len(recs)
    by_symbol = defaultdict(list)
    for d, sym, person, val in recs:
        by_symbol[sym].append((d, person, val))
    signals = []
    for sym, items in by_symbol.items():
        items.sort()
        fired_until = None
        for k, (d, _, _) in enumerate(items):
            last = bisect.bisect_right(cal, d) - 1   # the session the public date falls on (or before)
            if last < 0:
                continue
            first_day = cal[max(0, last - (S1_WINDOW - 1))]
            window = [(p, v) for dd, p, v in items[:k + 1] if first_day <= dd <= d]
            persons = {p for p, _ in window}
            total = sum((v for _, v in window), Decimal(0))
            if len(persons) >= S1_MIN_PERSONS and total >= S1_MIN_VALUE:
                if fired_until is not None and d <= fired_until:
                    continue                          # the same cluster, still open
                signals.append((d, sym, {"persons": len(persons), "value_inr": str(total)}))
                fired_until = cal[min(len(cal) - 1, last + S1_WINDOW)]
    info["clusters"] = len(signals)
    return signals, info


def s2_signals(db) -> tuple[list, dict]:
    rows = db.execute(text("""SELECT deal_date, symbol, upper(client) AS client, side,
        sum(quantity * price) AS value FROM altdata.deals GROUP BY deal_date, symbol, upper(client), side""")).all()
    book = defaultdict(lambda: {"BUY": Decimal(0), "SELL": Decimal(0)})
    for r in rows:
        if r.side in ("BUY", "SELL") and r.value is not None:
            book[(r.deal_date, r.symbol, r.client)][r.side] += r.value
    signals, seen = [], set()
    for (d, sym, client), v in book.items():
        if v["BUY"] >= S2_MIN_BUY_VALUE and v["SELL"] == 0 and (d, sym) not in seen:
            seen.add((d, sym))                       # several big buyers on one day are one signal
            signals.append((d, sym, {"client": client, "buy_value_inr": str(v["BUY"])}))
    return signals, {"client_days": len(book), "qualifying_stock_days": len(signals)}


def s5_signals(db, cal) -> tuple[list, dict]:
    rows = db.execute(text("""SELECT release, effective, index_name, symbol, periodic_review
        FROM altdata.index_exclusions""")).all()
    signals, seen = [], set()
    for r in rows:
        if not r.periodic_review or (r.release, r.symbol) in seen:
            continue
        seen.add((r.release, r.symbol))
        k = bisect.bisect_left(cal, r.effective) - 1     # the "close of" session before the effective date
        if k < 0:
            continue
        signals.append((cal[k], r.symbol, {"index": r.index_name, "effective": r.effective.isoformat(),
                                           "release": r.release}))
    return signals, {"exclusion_rows": len(rows), "periodic_review_signals": len(signals)}


def s4_trades(db, nifty: Bars, cal: list[date], cost_rt=INDEX_COST_RT):
    rows = db.execute(text("SELECT reporting_date, net_cr FROM altdata.fpi_daily ORDER BY reporting_date")).all()
    info = {"reporting_dates": len(rows), "first": str(rows[0][0]) if rows else None,
            "last": str(rows[-1][0]) if rows else None}
    hits = [(d, net) for d, net in rows if net is not None and net <= S4_THRESHOLD_CR]
    info["days_at_or_below_threshold"] = len(hits)
    info["in_sample"] = sum(d >= SAMPLE_START for d, _ in hits)
    # Unconditional: the same trade entered at every session's open in the sample.
    base = []
    for k, day in enumerate(cal):
        if day < SAMPLE_START:
            continue
        t = simulate(nifty, cal[k - 1], cal, nifty, cost_rt, add_dividend=True)
        if t is None:
            break
        base.append(t["net"])
    baseline = mean(base)
    info["unconditional_sessions"] = len(base)
    info["unconditional_mean_net_pct"] = baseline * 100
    trades, open_until = [], None
    for d, net in hits:
        if d < SAMPLE_START or (open_until and d < open_until):
            continue
        t = simulate(nifty, d, cal, nifty, cost_rt, add_dividend=True)
        if t is None:
            continue
        t["bench"] = baseline
        t["excess"] = t["net"] - baseline
        open_until = date.fromisoformat(t["exit"])
        trades.append({"public": d.isoformat(), "symbol": "NIFTY 50", "fpi_net_cr": str(net), **t})
    return trades, info


# ------------------------------------------------------------------ main --

def main() -> None:
    db = SessionLocal()
    require_lab_database(db.get_bind().url)
    nifty, cal = load_nifty()
    log.info("NIFTY pinned: %s → %s, %d sessions", cal[0], cal[-1], len(cal))
    universe = Universe()
    bars_by_id = load_stock_bars(db, universe.all_ids)
    log.info("bars loaded for %d of %d universe stocks", len(bars_by_id), len(universe.all_ids))
    intervals = bhav_isin_intervals()
    mapper = Mapper(db, intervals)

    results = {"experiment": "phase28_alternative_data", "alpha_bonferroni": ALPHA,
               "sample_start": SAMPLE_START.isoformat(), "price_data_end": cal[-1].isoformat(),
               "nifty_sessions": len(cal), "signals": {}}

    def stock_signal(key, signals, info):
        trades, counts = run_stock_signal(key, signals, mapper, universe, bars_by_id, cal, nifty)
        ev = evaluate(trades)
        entry = {"data": info, "funnel": counts, **ev, "trades_detail": trades}
        if ev["verdict"] == "PASS":
            entry["robustness"] = robustness(trades, lambda c: run_stock_signal(
                key + "@cost", signals, mapper, universe, bars_by_id, cal, nifty, cost_rt=c)[0])
        results["signals"][key] = entry

    s1, s1_info = s1_signals(db, cal)
    stock_signal("S1_insider_cluster_buy", s1, s1_info)
    s2, s2_info = s2_signals(db)
    stock_signal("S2_bulk_block_buy", s2, s2_info)
    results["signals"]["S3_top_fund_accumulation"] = {
        "verdict": "BLOCKED", "trades": 0,
        "reason": "No consolidated source of monthly scheme portfolios: AMFI links to each AMC's site, each "
                  "publishes Excel files in its own layout, and reading Excel needs openpyxl, which is not "
                  "in the approved stack."}
    s4, s4_info = s4_trades(db, nifty, cal)
    ev4 = evaluate(s4)
    results["signals"]["S4_fii_extreme_selling"] = {"data": s4_info, **ev4, "trades_detail": s4}
    if ev4["verdict"] == "PASS":
        results["signals"]["S4_fii_extreme_selling"]["robustness"] = robustness(
            s4, lambda c: s4_trades(db, nifty, cal, cost_rt=c)[0])
    s5, s5_info = s5_signals(db, cal)
    stock_signal("S5_index_deletion_recovery", s5, s5_info)
    s5_trades = results["signals"]["S5_index_deletion_recovery"]["trades_detail"]
    results["signals"]["S5_index_deletion_recovery"]["descriptive_by_index"] = {
        idx: {"trades": len(ts), "median_excess_pct": median(t["excess"] for t in ts) * 100}
        for idx in ("NIFTY 50", "NIFTY Next 50", "NIFTY 500")
        if (ts := [t for t in s5_trades if t["index"] == idx])}

    results["mapping"] = dict(mapper.stats)
    (OUT / "results.json").write_text(json.dumps(results, indent=1, default=str))
    print(f"\n{'Signal':32} {'Trades':>6} {'Median ex%':>10} {'Mean ex%':>9} {'Win%':>6} {'p':>8}  Verdict")
    for k, v in results["signals"].items():
        if v.get("trades"):
            print(f"{k:32} {v['trades']:>6} {v['median_excess_pct']:>10.2f} {v['mean_excess_pct']:>9.2f} "
                  f"{v['win_rate_pct']:>6.1f} {v['p_value']:>8.4f}  {v['verdict']}")
        else:
            print(f"{k:32} {v.get('trades', 0):>6} {'':>10} {'':>9} {'':>6} {'':>8}  {v['verdict']}")
    print(f"\nWritten to {OUT / 'results.json'}")


if __name__ == "__main__":
    main()
