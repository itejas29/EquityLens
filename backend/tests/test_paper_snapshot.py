"""The nightly paper-equity snapshot.

record_paper_snapshot used Decimal without importing it (since commit 0f1f89b,
2026-09-09), so every nightly call raised NameError, the scheduler logged it
and moved on, and no account got an equity-curve point after 2026-09-09.
Nothing exercised the function, which is how seven weeks went unnoticed.
"""

from decimal import Decimal

import pandas as pd

import app.services.forward_testing as forward_testing
from app.models.paper_trading import PaperEquitySnapshot
from app.models.user import User
from app.services.paper_trading import get_or_create_account


def test_the_nightly_equity_snapshot_is_recorded(db_session, monkeypatch):
    user = User(name="Paper", email="paper@example.com", password_hash="unused")
    db_session.add(user)
    db_session.flush()
    account = get_or_create_account(db_session, user.id)
    db_session.flush()
    db_session.refresh(account)
    day = account.created_at.date()
    # NIFTY is fetched from Yahoo in production; one known close stands in.
    monkeypatch.setattr(
        forward_testing, "fetch_price_history",
        lambda *a, **k: pd.DataFrame({"date": [day], "close": [22000.0]}),
    )

    snapshot = forward_testing.record_paper_snapshot(db_session, account.id, day)
    db_session.flush()

    assert db_session.query(PaperEquitySnapshot).count() == 1
    assert Decimal(snapshot.total_equity) == Decimal(account.virtual_capital)
    assert Decimal(snapshot.cumulative_return) == Decimal("0")
