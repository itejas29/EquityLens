import logging
from datetime import date as date_type
from datetime import timedelta

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models.daily_signal import DailySignal, SignalOutcome
from app.models.paper_trading import PaperAccount, PaperEquitySnapshot
from app.models.price_history import PriceHistory
from app.services.market_data import fetch_price_history
from app.services.paper_trading import get_account_summary

logger = logging.getLogger(__name__)


def _nifty_closes(start: date_type, end: date_type) -> dict[date_type, float]:
    """NIFTY 50 close per trading day in [start, end], keyed by date.

    NOT read from stocks/price_history via a join: NIFTY is an index, not a
    member of the 500-stock tradable universe, so there has never been (and
    should never be) a `stocks` row for it — adding one would be exactly the
    kind of universe change this project rules out. Fetched the same way
    compute_market_regime() already gets it: directly from Yahoo via ^NSEI.
    The still-forming session's row can come back with a NaN close (see the
    identical fix in compute_market_regime), so it is dropped here too.
    """
    df = fetch_price_history("^NSEI", period="2y")[["date", "close"]].dropna(subset=["close"])
    df = df[(df["date"] >= start) & (df["date"] <= end)]
    return {row.date: float(row.close) for row in df.itertuples()}


def record_paper_snapshot(db: Session, account_id: int, date: date_type) -> PaperEquitySnapshot:
    """Record a daily end-of-day snapshot of the paper trading portfolio."""
    # We will compute the snapshot for a specific account. The main paper account is for the single user.
    # In a real multi-user system we'd loop over active accounts.
    account = db.query(PaperAccount).filter(PaperAccount.id == account_id).first()
    if not account:
        raise ValueError(f"Account {account_id} not found")

    summary = get_account_summary(db, account.user_id)
    
    # Calculate NIFTY return over the same period (from account creation to today)
    # We need the nifty close on account.created_at.date() and on `date`
    nifty_return = None
    created_date = account.created_at.date()
    nifty_window = _nifty_closes(created_date, date)
    if nifty_window:
        window_dates = sorted(nifty_window.keys())
        nifty_start_close = nifty_window[window_dates[0]]
        nifty_end_close = nifty_window[window_dates[-1]]
        if nifty_start_close:
            nifty_return = float((nifty_end_close - nifty_start_close) / nifty_start_close * 100)
    
    # Calculate daily_return and cumulative_return
    # Cumulative is simply (equity - initial_capital) / initial_capital
    cumulative_return = (summary.equity - float(account.virtual_capital)) / float(account.virtual_capital) * 100
    
    # For daily return, get the snapshot from the previous trading day
    prev_snapshot = db.query(PaperEquitySnapshot).filter(
        PaperEquitySnapshot.account_id == account.id,
        PaperEquitySnapshot.date < date
    ).order_by(PaperEquitySnapshot.date.desc()).first()
    
    if prev_snapshot:
        prev_eq = float(prev_snapshot.total_equity)
        daily_return = (summary.equity - prev_eq) / prev_eq * 100 if prev_eq > 0 else 0.0
    else:
        # First day
        daily_return = cumulative_return
        
    snapshot = PaperEquitySnapshot(
        account_id=account.id,
        date=date,
        cash=summary.account.cash,
        portfolio_value=summary.market_value,
        total_equity=summary.equity,
        daily_return=round(daily_return, 4),
        cumulative_return=round(cumulative_return, 4),
        nifty_return=round(nifty_return, 4) if nifty_return is not None else None,
        drawdown=summary.current_drawdown_pct,
    )
    db.add(snapshot)
    return snapshot


def evaluate_signal_outcomes(db: Session, target_date: date_type) -> int:
    """Evaluate performance of all past signals against today's prices (1D, 5D, 10D, 20D horizons)."""
    
    # Fetch all signals that haven't hit their 20D mark yet, or just all signals where 20D is missing
    # To be safe, we just evaluate all active signals (where date >= target_date - 40 days)
    # 40 days covers weekends/holidays for a 20 trading day window.
    cutoff_date = target_date - timedelta(days=40)
    
    signals = db.query(DailySignal).filter(DailySignal.date >= cutoff_date).all()
    if not signals:
        return 0
        
    # We need a quick way to get NIFTY returns.
    nifty_closes = _nifty_closes(cutoff_date, target_date)

    # To count trading days correctly, we can use the nifty dates as our trading calendar
    calendar = sorted(list(nifty_closes.keys()))
    
    upserted_count = 0
    for signal in signals:
        # We need the price history of this stock from signal.date to target_date
        ph_rows = db.query(PriceHistory).filter(
            PriceHistory.stock_id == signal.stock_id,
            PriceHistory.date >= signal.date,
            PriceHistory.date <= target_date
        ).order_by(PriceHistory.date.asc()).all()
        
        if not ph_rows:
            continue
            
        ph_closes = {r.date: float(r.close) for r in ph_rows if r.close}
        ph_highs = {r.date: float(r.high) for r in ph_rows if r.high}
        ph_lows = {r.date: float(r.low) for r in ph_rows if r.low}
        
        if not ph_closes:
            continue
            
        current_price = ph_closes.get(target_date)
        if current_price is None:
            # Maybe stock didn't trade today, use last available
            last_date = max(ph_closes.keys())
            current_price = ph_closes[last_date]
            
        # Find index of signal.date in calendar
        try:
            start_idx = calendar.index(signal.date)
        except ValueError:
            # Signal date not in calendar, maybe it was a weekend run? Find next valid day
            future_days = [d for d in calendar if d > signal.date]
            if not future_days:
                continue
            start_idx = calendar.index(future_days[0])
            
        # Evaluate 1D, 5D, 10D, 20D
        def get_return_for_horizon(days: int) -> tuple[float | None, float | None]:
            if start_idx + days < len(calendar):
                eval_date = calendar[start_idx + days]
                if eval_date in ph_closes and signal.date in nifty_closes and eval_date in nifty_closes:
                    stk_ret = (ph_closes[eval_date] - float(signal.reference_close)) / float(signal.reference_close) * 100
                    nft_ret = (nifty_closes[eval_date] - nifty_closes[signal.date]) / nifty_closes[signal.date] * 100
                    return stk_ret, nft_ret
            # If horizon hasn't elapsed, return current return up to target_date (Mark-to-market)
            # Actually, forward track record needs strict N-day horizons.
            # If not reached, leave None.
            return None, None
            
        r1d, n1d = get_return_for_horizon(1)
        r5d, n5d = get_return_for_horizon(5)
        r10d, n10d = get_return_for_horizon(10)
        r20d, n20d = get_return_for_horizon(20)
        
        # Calculate excursions and target/stop hits
        max_fav = 0.0
        max_adv = 0.0
        target_hit = False
        stop_hit = False
        
        for d in sorted(ph_closes.keys()):
            if d <= signal.date:
                continue
            if d > target_date:
                break
                
            high = ph_highs.get(d, ph_closes[d])
            low = ph_lows.get(d, ph_closes[d])
            
            fav = (high - float(signal.reference_close)) / float(signal.reference_close) * 100
            adv = (low - float(signal.reference_close)) / float(signal.reference_close) * 100
            
            if fav > max_fav:
                max_fav = fav
            if adv < max_adv:
                max_adv = adv
                
            if high >= float(signal.target_price):
                target_hit = True
            if low <= float(signal.stop_loss):
                stop_hit = True
                
        # Upsert Outcome
        outcome = db.query(SignalOutcome).filter(SignalOutcome.signal_id == signal.id).first()
        if not outcome:
            outcome = SignalOutcome(signal_id=signal.id)
            db.add(outcome)
            
        outcome.evaluated_at = target_date
        outcome.current_price = current_price
        outcome.return_1d = round(r1d, 4) if r1d is not None else outcome.return_1d
        outcome.return_5d = round(r5d, 4) if r5d is not None else outcome.return_5d
        outcome.return_10d = round(r10d, 4) if r10d is not None else outcome.return_10d
        outcome.return_20d = round(r20d, 4) if r20d is not None else outcome.return_20d
        
        outcome.max_favorable_excursion = round(max_fav, 4)
        outcome.max_adverse_excursion = round(max_adv, 4)
        outcome.target_hit = target_hit
        outcome.stop_hit = stop_hit
        
        outcome.nifty_return_1d = round(n1d, 4) if n1d is not None else outcome.nifty_return_1d
        outcome.nifty_return_5d = round(n5d, 4) if n5d is not None else outcome.nifty_return_5d
        outcome.nifty_return_10d = round(n10d, 4) if n10d is not None else outcome.nifty_return_10d
        outcome.nifty_return_20d = round(n20d, 4) if n20d is not None else outcome.nifty_return_20d
        
        upserted_count += 1
        
    return upserted_count
