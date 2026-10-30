"""Phase 24 data, stage 2 — build both universes and load the missing names.

Pre-registration: docs/experiments/phase24_survivorship/PREREGISTRATION.md.

Reads the NSE archive fetched by phase24_fetch_nse_archive.py and the Phase 23
lab database (its 1,000-stock pool must be in place). For each fold start:

  B  top 500 NSE EQ securities by mean daily traded value over the 20 trading
     days up to the fold start — every security that traded then;
  A  the same ranking restricted to securities in today's pool.

A security is one IDENTITY across renames and face-value splits: bhavcopy rows
are linked when they share a symbol (a split changes the ISIN, not the symbol)
or an ISIN (a rename changes the symbol, not the ISIN). A linked group in which
two different ISINs trade on the same day is not one security — a reused
symbol, say — and is split back into its ISINs.

Names in B that are not pool stocks are loaded into the lab, inactive so no
earlier phase's script sees them:
  - still listed today: Yahoo history and metadata, exactly as the pool was;
  - no longer listed: NSE bhavcopy prices, back-adjusted for bonuses and splits
    from NSE's corporate-actions list (services/nse_archive.py), stored under
    the symbol "DL:<last symbol>" with its own sector bucket.

Writes universes.json into experiment_dir("phase24_survivorship"). Run against
the lab only, never production.
"""

import json
import logging
import os
import sys
import time
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.database import SessionLocal  # noqa: E402
from app.core.experiment_paths import experiment_dir  # noqa: E402
from app.models.nse_symbol import NseSymbol  # noqa: E402
from app.models.stock import Stock  # noqa: E402
from app.services import universe as universe_mod  # noqa: E402
from app.services.ingestion import upsert_price_history, upsert_stock  # noqa: E402
from app.services.market_data import fetch_stock_meta  # noqa: E402
from app.services.nse_archive import adjust_series, adjustment_factor, parse_bhavcopy, parse_ex_date  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("p24prep")

ROOT = Path(os.environ.get("NSE_ARCHIVE_DIR", Path.home() / ".cache/equitylens/nse")).expanduser()
OUT = experiment_dir("phase24_survivorship")
PRODUCTION_HOST_FRAGMENT = "ep-aged-scene-a6dw1x3w"

# Phase 20/23's fold layout, unchanged.
START, END = date(2016, 10, 1), date(2026, 8, 1)
TRAIN, TEST, ROLL = 18, 6, 6
TOP_N, LIQ_DAYS = 500, 20


def folds() -> list[tuple[int, date, date]]:
    out, s, k = [], START, 0
    while True:
        te = s + timedelta(days=int(TRAIN * 30.44))
        tt = te + timedelta(days=int(TEST * 30.44))
        if tt > END:
            return out
        k += 1
        out.append((k, te, tt))
        s = s + timedelta(days=int(ROLL * 30.44))


def load_eq_rows() -> pd.DataFrame:
    rows = []
    files = sorted((ROOT / "bhavcopy").glob("*.zip"))
    for i, f in enumerate(files, 1):
        d = date(int(f.stem[:4]), int(f.stem[4:6]), int(f.stem[6:8]))
        for r in parse_bhavcopy(f.read_bytes(), d):
            # Company shares only. NSE trades ETFs in the EQ series too
            # (NIFTYBEES, GOLDBEES, LIQUIDBEES...), and their traded value wins
            # them top-500 slots. An Indian ISIN says what a security is:
            # INE for company equity, INF for mutual-fund units, ETFs included.
            # Today's pool comes from NSE's equity list, which has no ETFs, so
            # without this B would differ from A by more than survivorship.
            if r.series == "EQ" and (r.isin or "").startswith("INE"):
                rows.append((d, r.symbol, r.isin or "", float(r.open), float(r.high), float(r.low),
                             float(r.close), r.volume, float(r.traded_value)))
        if i % 250 == 0:
            log.info("parsed %d/%d daily files (%d EQ rows)", i, len(files), len(rows))
    df = pd.DataFrame(rows, columns=["date", "symbol", "isin", "open", "high", "low", "close", "volume", "value"])
    return df.astype({"symbol": "category", "isin": "category"})


def link_identities(df: pd.DataFrame) -> pd.Series:
    """Entity id per row: union-find over symbols and ISINs, then any group
    with two ISINs trading on one day split back into its ISINs."""
    parent: dict[str, str] = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    pairs = df[["symbol", "isin"]].drop_duplicates()
    for sym, isin in pairs.itertuples(index=False):
        a, b = find(f"S:{sym}"), find(f"I:{isin}" if isin else f"S:{sym}")
        if a != b:
            parent[b] = a
    group = pd.Series([find(f"S:{s}") for s in df["symbol"]], index=df.index)

    clash = df.assign(g=group).groupby(["g", "date"], observed=True)["isin"].nunique()
    bad = set(clash[clash > 1].index.get_level_values(0))
    if bad:
        log.warning("%d linked groups had two ISINs trading on one day — split back by ISIN", len(bad))
        mask = group.isin(bad)
        group[mask] = "I:" + df.loc[mask, "isin"].astype(str)
    return group


def main() -> None:
    db = SessionLocal()
    if PRODUCTION_HOST_FRAGMENT in str(db.get_bind().url):
        raise SystemExit("Refusing: this writes stocks and prices, and DATABASE_URL is production.")

    df = load_eq_rows()
    log.info("EQ rows: %d, symbols: %d, trading days: %d", len(df), df["symbol"].nunique(), df["date"].nunique())
    df["entity"] = link_identities(df)

    # What exists today, from the catalogue the lab loaded (NSE's EQUITY_L).
    catalogue = {n.symbol: n.isin for n in db.query(NseSymbol).all()}
    listed_isins = {i for i in catalogue.values() if i}
    symbol_by_isin = {i: s for s, i in catalogue.items() if i}
    pool = {s.symbol: s.id for s in db.query(Stock).filter(Stock.is_active == True).all()}  # noqa: E712
    pool_by_isin = {catalogue[s]: sid for s, sid in pool.items() if catalogue.get(s)}

    info = {}
    for ent, g in df.groupby("entity", observed=True):
        last = g.loc[g["date"].idxmax()]
        isins = set(g["isin"].astype(str)) - {""}
        syms = set(g["symbol"].astype(str))
        pool_id = (pool.get(str(last["symbol"]))
                   or next((pool_by_isin[i] for i in isins if i in pool_by_isin), None)
                   or next((pool[s] for s in syms if s in pool), None))
        current_symbol = next((symbol_by_isin[i] for i in sorted(isins) if i in symbol_by_isin), None)
        info[ent] = {"last_symbol": str(last["symbol"]), "last_date": last["date"], "isins": sorted(isins),
                     "symbols": sorted(syms), "pool_id": pool_id,
                     "listed_today": bool(isins & listed_isins) or str(last["symbol"]) in catalogue,
                     "current_symbol": current_symbol}

    trading_days = sorted(df["date"].unique())
    plan = []
    needed: set[str] = set()
    for k, te, tt in folds():
        window = [d for d in trading_days if d <= te][-LIQ_DAYS:]
        liq = (df[df["date"].isin(window)].groupby("entity", observed=True)["value"].mean()
               .sort_values(ascending=False))
        ranked = list(liq.index)
        b = ranked[:TOP_N]
        a = [e for e in ranked if info[e]["pool_id"] is not None][:TOP_N]
        needed |= {e for e in b if info[e]["pool_id"] is None}
        plan.append({"fold": k, "test": [te.isoformat(), tt.isoformat()], "A": a, "B": b,
                     "liquidity_window": [window[0].isoformat(), window[-1].isoformat()]})
        log.info("fold %2d %s: B has %d pool names, %d listed elsewhere, %d no longer listed", k, te,
                 sum(info[e]["pool_id"] is not None for e in b),
                 sum(info[e]["pool_id"] is None and info[e]["listed_today"] for e in b),
                 sum(not info[e]["listed_today"] for e in b))

    # --- load the names B needs that the pool lacks ---------------------------
    still_listed = sorted(e for e in needed if info[e]["listed_today"])
    gone = sorted(e for e in needed if not info[e]["listed_today"])
    log.info("to load: %d still listed (Yahoo), %d no longer listed (NSE bhavcopy)", len(still_listed), len(gone))

    entity_stock: dict[str, int] = {e: info[e]["pool_id"] for e in info if info[e]["pool_id"] is not None}

    fallback: list[str] = []  # listed today, but Yahoo has no history: NSE's own prices instead
    yahoo_symbols = {e: (info[e]["current_symbol"] or info[e]["last_symbol"]) for e in still_listed}
    have = {sym for (sym,) in db.query(Stock.symbol).filter(
        Stock.symbol.in_(set(yahoo_symbols.values())), Stock.is_active == False).all()}  # noqa: E712
    fetched, failures = universe_mod._ingest_prices_batched(
        db, sorted(set(yahoo_symbols.values()) - have)) if set(yahoo_symbols.values()) - have else ({}, [])
    stored = {**{sym: 0 for sym in have}, **fetched}
    log.info("Yahoo: %d stored, %d failed", len(stored), len(failures))
    for e, sym in yahoo_symbols.items():
        if sym in stored:
            st = db.query(Stock).filter(Stock.symbol == sym).first()
            if sym not in have:
                try:
                    st = upsert_stock(db, sym, fetch_stock_meta(sym))
                except Exception:  # metadata is best-effort; prices are what matter
                    pass
                time.sleep(0.9)
            st.is_active = False
            entity_stock[e] = st.id
            db.commit()
        else:
            fallback.append(e)
    log.info("Yahoo metadata done; %d listed names fall back to bhavcopy prices", len(fallback))
    gone = gone + fallback

    actions_by_key: dict[str, list[tuple[date, object]]] = defaultdict(list)
    for f in sorted(ROOT.glob("corporate_actions_*.json")):
        for rec in json.loads(f.read_text()):
            factor = adjustment_factor(rec.get("subject", ""))
            ex = parse_ex_date(rec.get("exDate", ""))
            if factor is None or ex is None:
                continue
            for key in (f"S:{rec.get('symbol')}", f"I:{rec.get('isin')}"):
                actions_by_key[key].append((ex, factor))

    rows_by_entity = {e: g for e, g in df[df["entity"].isin(set(gone))].groupby("entity", observed=True)}
    adjusted_names = 0
    for e in gone:
        g = rows_by_entity[e].sort_values("date")
        acts = sorted({a for s in info[e]["symbols"] for a in actions_by_key.get(f"S:{s}", [])}
                      | {a for i in info[e]["isins"] for a in actions_by_key.get(f"I:{i}", [])})
        bars = [(r.date, *(Decimal(str(round(x, 2))) for x in (r.open, r.high, r.low, r.close)), int(r.volume))
                for r in g.itertuples()]
        if acts:
            adjusted_names += 1
            bars = adjust_series(bars, acts)
        symbol = ("DL:" + info[e]["last_symbol"])[:20]
        st = db.query(Stock).filter(Stock.symbol == symbol).first() or Stock(symbol=symbol, is_active=False)
        st.is_active = False
        st.sector = f"Delisted {info[e]['last_symbol']}"[:100]
        db.add(st)
        db.flush()
        upsert_price_history(db, st.id, pd.DataFrame(bars, columns=["date", "open", "high", "low", "close", "volume"]))
        db.commit()
        entity_stock[e] = st.id
    log.info("bhavcopy names loaded: %d (%d with bonus/split adjustments)", len(gone), adjusted_names)

    bhavcopy_ids = {entity_stock[e] for e in gone if e in entity_stock}
    for p in plan:
        for arm in ("A", "B"):
            ents = p[arm]
            p[f"{arm}_missing"] = [info[e]["last_symbol"] for e in ents if e not in entity_stock]
            p[arm] = [entity_stock[e] for e in ents if e in entity_stock]
        p["B_from_bhavcopy"] = sum(1 for sid in p["B"] if sid in bhavcopy_ids)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "universes.json").write_text(json.dumps({
        "built": time.strftime("%Y-%m-%d %H:%M"), "top_n": TOP_N, "liquidity_days": LIQ_DAYS,
        "loaded_from_bhavcopy": len(gone), "listed_but_no_yahoo_history": len(fallback),
        "adjusted_for_bonus_or_split": adjusted_names,
        "folds": plan}, indent=1))
    log.info("wrote %s", OUT / "universes.json")


if __name__ == "__main__":
    main()
