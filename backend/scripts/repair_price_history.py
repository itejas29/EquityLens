"""Repair stored price history that the ingest wrote wrong.

Two repairs, both re-fetch from the provider and overwrite. Neither computes a
price: every value written comes from the provider, because back-adjusting our
own stored series would be deriving prices, which build rule 5 forbids.

  --date YYYY-MM-DD   Re-fetch that one session for every active stock.
                      For the 2026-08-25 incident, where a catch-up run
                      executed 63 minutes into the session and wrote in-progress
                      bars as daily closes.

  --symbols A,B       Re-pull each symbol's ENTIRE history at period="max".
                      For a provider restatement (split/bonus) that landed
                      before the incremental overlap window could see it, so
                      the stored series is split across two price bases.

                      period="max", not universe_config.HISTORY_PERIOD: a 10y
                      pull for TDPOWERSYS starts 2016-09-12 while our stored
                      series starts 2016-08-16, which would leave 18 bars on
                      the old basis and move the discontinuity rather than
                      remove it. "max" reaches 2011 and covers everything.

Dry run by default. Pass --apply to write.

  python scripts/repair_price_history.py --date 2026-08-25 --symbols TDPOWERSYS --apply
"""

import argparse
import datetime as dt
import logging
import sys

sys.path.insert(0, "/app")

from app.core.database import SessionLocal
from app.core.memory_hygiene import trim_every
from app.core.universe_config import DOWNLOAD_BATCH_SIZE
from app.models.price_history import PriceHistory
from app.models.stock import Stock
from app.services.incremental import _drop_unsettled_session, _normalise_df
from app.services.indicators import compute_indicators, fetch_benchmark_df, load_price_history_df
from app.services.ingestion import upsert_indicators, upsert_price_history
from app.services.market_data import download_batch_with_retry, fetch_price_history, ticker_frame

logging.basicConfig(level=logging.WARNING, format="%(message)s")
log = logging.getLogger("repair")

# A stored and a re-fetched close for the same date should agree to the cent.
# Anything above this is a real disagreement, not Numeric(12,2)-vs-float64 noise.
DISAGREE_PCT = 0.001


def repair_date(db, day: dt.date, apply: bool) -> set[int]:
    """Re-fetch one session for every active stock. Returns repaired stock ids."""
    stocks = db.query(Stock).filter(Stock.is_active == True).order_by(Stock.symbol).all()  # noqa: E712
    by_id = {s.id: s.symbol for s in stocks}
    stored = {
        sid: (float(c), float(v))
        for sid, c, v in db.query(PriceHistory.stock_id, PriceHistory.close, PriceHistory.volume)
        .filter(PriceHistory.date == day, PriceHistory.stock_id.in_(by_id))
        .all()
    }
    print(f"\n=== date repair {day} — {len(stocks)} active stocks, {len(stored)} with a stored bar")

    repaired: set[int] = set()
    unchanged = missing = 0

    for i in range(0, len(stocks), DOWNLOAD_BATCH_SIZE):
        chunk = stocks[i : i + DOWNLOAD_BATCH_SIZE]
        tickers = [f"{s.symbol}.NS" for s in chunk]
        try:
            raw = download_batch_with_retry(
                tickers=tickers, start=day, end=day + dt.timedelta(days=1),
                interval="1d", group_by="ticker", auto_adjust=False,
            )
        except Exception as exc:
            log.warning("batch %d failed: %s", i // DOWNLOAD_BATCH_SIZE + 1, exc)
            continue

        for s, t in zip(chunk, tickers):
            try:
                df = ticker_frame(raw, t)
            except (KeyError, IndexError):
                missing += 1
                continue
            if df is None or df.empty:
                missing += 1
                continue
            df = df.dropna(subset=["Close"])
            if df.empty:
                missing += 1
                continue

            frame, _ = _drop_unsettled_session(_normalise_df(df))
            frame = frame[frame["date"] == day]
            if frame.empty:
                missing += 1
                continue

            true_close = float(frame.iloc[0]["close"])
            have = stored.get(s.id)
            if have and have[0] and abs(true_close - have[0]) / have[0] * 100 <= DISAGREE_PCT:
                unchanged += 1
                continue

            if apply:
                upsert_price_history(db, s.id, frame)
                db.commit()
            repaired.add(s.id)
            if len(repaired) <= 8:
                was = f"{have[0]:.2f}" if have else "absent"
                print(f"  {s.symbol:<14} {was:>10} -> {true_close:>10.2f}")

        print(f"  batch {i // DOWNLOAD_BATCH_SIZE + 1}/"
              f"{(len(stocks) + DOWNLOAD_BATCH_SIZE - 1) // DOWNLOAD_BATCH_SIZE} done", flush=True)

    print(f"  repaired={len(repaired)} already_correct={unchanged} no_provider_bar={missing}")
    return repaired


def repair_symbols(db, symbols: list[str], apply: bool) -> set[int]:
    """Re-pull each symbol's full history at period='max'."""
    repaired: set[int] = set()
    print(f"\n=== full re-pull (period='max') — {len(symbols)} symbol(s)")

    for sym in symbols:
        stock = db.query(Stock).filter(Stock.symbol == sym).one_or_none()
        if stock is None:
            print(f"  {sym:<14} not in universe — skipped")
            continue

        before = {
            d: float(c)
            for d, c in db.query(PriceHistory.date, PriceHistory.close)
            .filter(PriceHistory.stock_id == stock.id).all()
        }
        try:
            frame = fetch_price_history(f"{sym}.NS", period="max")
        except Exception as exc:
            print(f"  {sym:<14} fetch failed: {exc}")
            continue

        frame, unsettled = _drop_unsettled_session(frame)
        fetched = {r.date: float(r.close) for r in frame.itertuples(index=False)}
        common = set(before) & set(fetched)
        differing = [d for d in common
                     if before[d] and abs(fetched[d] - before[d]) / before[d] * 100 > DISAGREE_PCT]
        stranded = sorted(d for d in before if d < min(fetched)) if fetched else sorted(before)

        print(f"  {sym:<14} stored={len(before)} fetched={len(fetched)} overlap={len(common)} "
              f"differing={len(differing)} stranded={len(stranded)} unsettled_dropped={unsettled}")
        if differing:
            d = min(differing)
            print(f"                 first difference {d}: {before[d]:.2f} -> {fetched[d]:.2f} "
                  f"(x{before[d] / fetched[d]:.4f})")
        if stranded:
            # Bars older than anything the provider will return. They cannot be
            # verified or corrected, and leaving them on the old basis puts a
            # fake gap at the seam. Report loudly rather than deleting silently.
            print(f"                 WARNING {len(stranded)} bars predate the provider's earliest "
                  f"({stranded[0]}..{stranded[-1]}) and will keep their current basis")

        if apply and differing:
            upsert_price_history(db, stock.id, frame)
            db.commit()
        if differing:
            repaired.add(stock.id)

    return repaired


def recompute_indicators(db, stock_ids: set[int], apply: bool) -> None:
    """Indicators are recursive (Wilder RSI, MACD EMA) and were computed from
    the wrong bars, so every repaired stock needs its series rebuilt."""
    if not stock_ids:
        return
    print(f"\n=== recomputing indicators for {len(stock_ids)} stock(s)")
    if not apply:
        print("  (dry run — skipped)")
        return

    # period="max", matching the re-pull: a 10y benchmark would leave beta and
    # relative strength NaN for the years a "max" symbol pull reaches back into.
    benchmark = fetch_benchmark_df(period="max")
    done = failed = 0
    for i, sid in enumerate(sorted(stock_ids), 1):
        try:
            # Unbounded on purpose, unlike the nightly ingest: a full re-pull
            # changes the price basis of the WHOLE series, so bounding this to
            # the 252-day indicator lookback would leave every older indicator
            # row derived from the pre-repair basis. trim_every below is what
            # keeps the unbounded loop's memory flat across ~500 stocks.
            price_df = load_price_history_df(db, sid)
            if price_df.empty:
                continue
            upsert_indicators(db, sid, compute_indicators(price_df, benchmark))
            db.commit()
            done += 1
        except Exception as exc:
            db.rollback()
            failed += 1
            log.warning("  stock_id=%s indicator recompute failed: %s", sid, exc)
        trim_every(i, every=25)
        if i % 50 == 0:
            print(f"  {i}/{len(stock_ids)}", flush=True)
    print(f"  recomputed={done} failed={failed}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--date", help="re-fetch this session for every active stock (YYYY-MM-DD)")
    ap.add_argument("--symbols", help="comma-separated symbols to re-pull in full")
    ap.add_argument("--apply", action="store_true", help="write (default is a dry run)")
    args = ap.parse_args()

    if not args.date and not args.symbols:
        ap.error("nothing to do — pass --date and/or --symbols")

    print("DRY RUN — nothing will be written. Pass --apply to repair."
          if not args.apply else "APPLYING repairs.")

    db = SessionLocal()
    try:
        touched: set[int] = set()
        if args.symbols:
            touched |= repair_symbols(db, [s.strip() for s in args.symbols.split(",") if s.strip()],
                                      args.apply)
        if args.date:
            touched |= repair_date(db, dt.date.fromisoformat(args.date), args.apply)
        recompute_indicators(db, touched, args.apply)
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
