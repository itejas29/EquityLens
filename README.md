# EquityLens

A research and paper-trading platform for NSE (Indian stock exchange) equities: scores ~40 stocks across four dimensions (technical, fundamental, valuation, risk), builds risk-sized portfolios, backtests the strategy point-in-time, trains a small secondary ML signal, and lets you paper-trade against real prices.

**This is a research tool, not investment advice.** No real-money execution. Past performance does not indicate future returns.

## What it does

- Pulls NSE price history and fundamentals from Yahoo Finance (`yfinance`)
- Computes technical indicators from formula (SMA, Wilder RSI, MACD, ATR, rolling volatility/beta/drawdown) — no `ta-lib`, no `pandas-ta`
- Scores every stock 0–100 on four sub-scores (technical / fundamental / valuation / risk), each with documented normalization and missing-data handling, combined into a weighted composite with a signal label
- Computes ATR-based entry zone / stop-loss / target for each stock
- Builds a capital-sized, sector-capped, risk-appetite-aware portfolio from the scored universe
- Backtests that strategy with genuine point-in-time correctness (no look-ahead), transaction costs, and slippage, compared against a NIFTY 50 buy-and-hold benchmark
- Trains a small secondary ML model (LogisticRegression / RandomForest) as an *additional* probability signal — never blended into the rule-based score
- Supports paper trading (virtual buy/sell against real prices) and a watchlist
- Redis caching + per-user rate limiting on the compute-heavy endpoints

## Architecture

```
┌─────────────┐      ┌──────────────────┐      ┌─────────────┐
│   React     │─────▶│  FastAPI backend  │◀────▶│  PostgreSQL │
│  (Vite SPA) │      │  (app/api/v1/*)   │      └─────────────┘
└─────────────┘      │                   │      ┌─────────────┐
                      │  app/services/*   │◀────▶│    Redis    │
                      │  app/ml/*         │      └─────────────┘
                      └─────────┬─────────┘
                                │
                                ▼
                      ┌──────────────────┐
                      │ yfinance (Yahoo   │
                      │ Finance) — live   │
                      │ NSE price/        │
                      │ fundamentals data │
                      └──────────────────┘
```

Backend layout: `app/models` (SQLAlchemy) → `app/schemas` (Pydantic) → `app/services` (business logic: scoring, levels, position sizing, portfolio construction, backtesting, paper trading) → `app/api/v1` (FastAPI routers). `app/ml` is a separate, self-contained package (features/train/predict) that reads from the same DB but never writes into the scoring path.

## Setup

### Docker Compose (all services)

```bash
cp .env.example .env
# edit .env — set JWT_SECRET_KEY to a real random value
docker compose build
docker compose up -d
```

Backend: `http://localhost:8000`, Frontend: `http://localhost:3000`, Postgres: `localhost:5432`, Redis: `localhost:6380` (host-side; internally `redis:6379` — 6380 avoids clashing with a locally-installed Redis that may already own 6379 on the host machine).

Both Docker images were built and independently verified working (`docker run` against the compose network — DB connectivity, migrations, real API responses, and the frontend serving via nginx all confirmed) during development. In this specific development sandbox, `docker compose up`'s own orchestration step intermittently hung under heavy host memory pressure unrelated to the compose file itself (`docker compose config` validates cleanly and `docker compose build` succeeds); if you hit the same thing, `docker compose up -d --no-build` after freeing memory, or starting services individually, works around it.

### Local dev (without Docker for the app processes)

```bash
# Postgres + Redis only
docker compose up -d postgres redis

# Backend
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # then edit REDIS_URL port to match (6380 if using the compose file above)
alembic upgrade head
python scripts/seed_universe.py              # seeds ~40 NSE stocks
python -m app.ml.train                        # optional: trains the ML signal
uvicorn app.main:app --reload

# Frontend
cd frontend
npm install
npm run dev
```

## API reference

All endpoints are under `/api/v1`. `*` = requires `Authorization: Bearer <token>`.

| Method | Path | Notes |
|---|---|---|
| GET | `/health` | DB connectivity check |
| POST | `/auth/register` | |
| POST | `/auth/login` | |
| GET | `/auth/me` * | |
| GET | `/stocks` | paginated, `?sector=` filter |
| GET | `/stocks/search` | `?q=&limit=`, searches all NSE listings |
| POST | `/stocks/{symbol}/ingest` | on-demand fetch + index, makes a symbol analysable |
| POST | `/stocks/catalogue/refresh` | reload the NSE equity list |
| GET | `/stocks/{symbol}` | cached 15m |
| GET | `/stocks/{symbol}/prices` | `?from=&to=`, never cached |
| POST | `/stocks/{symbol}/refresh` | re-fetch from yfinance, invalidates caches |
| POST | `/stocks/{symbol}/compute-indicators` | |
| GET | `/stocks/{symbol}/indicators` | `?from=&to=` |
| GET | `/daily-signals` | `?date=`, defaults to most recent published shortlist |
| GET | `/daily-signals/dates` | dates with a published shortlist |
| POST | `/daily-signals/run` | manual trigger for the 09:15 IST job, rate-limited |
| POST | `/stocks/{symbol}/score` | rate-limited |
| POST | `/scoring/run-universe` | rate-limited, invalidates scored-universe cache |
| GET | `/recommendations` | `?min_score=&sector=&limit=`, rate-limited |
| POST | `/portfolio/analyze` | rate-limited, capital-dependent, not cached |
| POST | `/portfolio` * | saves an analysis as a portfolio |
| GET | `/portfolio` * | with live mark-to-market P&L |
| POST | `/backtest` | rate-limited, cached 24h by config hash |
| GET | `/backtest/{id}` | |
| GET | `/watchlist` * | |
| POST | `/watchlist` * | |
| DELETE | `/watchlist/{symbol}` * | |
| POST | `/paper/buy` * | same transaction cost model as backtesting |
| POST | `/paper/sell` * | |
| GET | `/paper/account` * | mark-to-market, realized/unrealized P&L, win rate, drawdown |

## Universe: catalogue vs. ingested

Two populations, deliberately separate:

- **`nse_symbols`** — the searchable catalogue. Every NSE-listed equity, loaded
  from NSE's own published `EQUITY_L.csv` (**2,406 symbols**, 2,126 of them in
  the `EQ` rolling-settlement series). One HTTP request for the whole exchange,
  no per-symbol market-data calls. This is what `/stocks/search` queries.
- **`stocks`** — the ingested set. Symbols with real price history, indicators
  and fundamentals stored, which is what can actually be scored.

Search covers the whole exchange; results are annotated with `is_ingested` so a
client can tell "we have data on this" from "this exists and can be pulled".
Opening a symbol that isn't ingested yet pulls it on demand —
measured at **~3 seconds** for 500 price rows plus 500 indicator rows.

### How the scored universe is built

`scripts/build_universe.py` screens the whole exchange in two stages, because
yfinance batches price history but has no batch endpoint for fundamentals.

| Stage | What it does | Measured |
|---|---|---|
| 1. Screen | Batched 3mo prices for all 2,126 listings → 20-day average traded value → keep the top N | **~14 min**, nothing written |
| 2. Ingest | Full 2y history (batched) + fundamentals (serial) + indicators for survivors | **~17 min** for 500 |

One measured run: 2,126 listings considered, 2,125 returned price data, 500
cleared the ₹5cr/day floor (the `MAX_UNIVERSE_SIZE` cap bound before the floor
did), 500 ingested and indexed. Scoring all 500 then takes **~3 seconds**.

Bulk-ingesting all 2,126 was rejected on purpose — at ~0.83s per fundamentals
call that alone is ~29 minutes every run, and the extra names are too illiquid
for the published ATR levels to be executable.

**Rate limiting is the main failure mode.** Stage 2 originally ran with a 0.4s
delay and hit `YFRateLimitError` immediately; because `fetch_stock_meta` turns
an exhausted retry into `SymbolNotFoundError`, ~60 symbols were recorded as
"no data" when they were merely throttled. The delay is now 0.9s with a 30s gap
between stages, and `--repair` re-runs stage 2 for any stock left with prices
but no indicators without repeating the 14-minute screen.

`is_active` is what every downstream query filters on — stocks that fall below
the floor are deactivated, never deleted, so their history and past
recommendations survive.

**Caveat that matters:** the fundamental and valuation sub-scores are percentile
ranks *against the ingested universe*, so ingesting a new stock shifts every
other stock's score. Scores are therefore comparable within a scoring run, not
across runs that had different universes. `POST /stocks/{symbol}/ingest`
invalidates the scored-universe cache for exactly this reason.

## Daily shortlist

A background task publishes a dated, frozen shortlist once per trading day at
09:15 IST (`app/core/scheduler.py` → `daily_signals_loop`). It is the app's
default screen (`/today`); the full scored universe stays available behind it.

It differs from `/recommendations` in three ways:

- **Gated.** A stock is excluded unless both price-derived sub-scores
  (technical, risk) are present, ATR levels are computable, and its most recent
  close is under 7 days old. The scoring engine renormalizes around missing
  inputs by design, which means a stock with no usable price series can still
  produce a composite from fundamentals and valuation alone — fine for a
  research table, not for a dated entry level. Those are dropped rather than
  shown with a caveat. Thresholds live in `app/core/daily_signals_config.py`.
- **Frozen.** Scores and levels are written once for the date and never
  recomputed, so a past day's shortlist can be reviewed as it was published.
  Live price is compared against the frozen zone at read time only.
- **Explained.** Each entry carries plain-English clauses generated from the
  same computed values that produced the score — trend vs. the 50/200-day
  averages, RSI band, MACD sign, beta, max drawdown, P/E vs. sector median,
  revenue growth. Negatives are emitted as `caution` clauses and shown
  alongside the positives, never suppressed.

Capped at 8 names with a 2-per-sector limit. Re-running for a date replaces
that date's rows.

The UI states a direct call — BUY NOW / BUY / WAIT — derived from where the
live price sits against the frozen entry zone. Price above the zone is always
a WAIT: past the entry range the stop is proportionally further away, which
breaks the 1:2 the target was set for. This is a private tool for a known
group, not a published research product; the decisive wording does not come
with any claim of accuracy, and the measured backtest below still shows the
strategy underperforming a NIFTY 50 buy-and-hold over the tested window.

## Scoring methodology

Four sub-scores (0–100 each), weighted into a composite:

```
overall = 0.30·technical + 0.30·fundamental + 0.20·valuation + 0.20·risk
```

**Technical (internal weights)** — fixed-band mapping, not percentile (these are bounded/self-referential, not peer-relative):
`price_vs_dma50` 0.25 · `golden_cross` (50dma vs 200dma) 0.25 · `rsi_band` 0.20 · `macd_momentum` (sign + slope) 0.20 · `volume_confirmation` 0.10

**Fundamental** — percentile rank vs. the active universe:
`roe` 0.20 · `revenue_growth` 0.15 · `eps_growth` 0.15 · `profit_growth` 0.15 · `roce` 0.15 · `debt_to_equity` (inverted) 0.10 · `operating_margin` 0.10

**Valuation** — percentile vs. sector peers / own trailing range:
`pe_vs_sector` 0.30 · `pe_vs_own_range` 0.25 · `pb_vs_sector` 0.25 · `growth_adjusted_pe` (PEG-style) 0.20

**Risk** — higher score = *lower* risk, kept consistent everywhere:
`volatility` (inverted) 0.30 · `beta` (fixed-band, anchored at 1.0) 0.25 · `max_drawdown` (inverted) 0.25 · `liquidity` (20d avg traded value) 0.20

**Missing data**: a NULL metric is dropped and its sub-score's weights renormalize over what's left; if more than half a sub-score's inputs are missing, the whole sub-score goes NULL and the composite renormalizes across whatever sub-scores survive. Nothing is ever backfilled with a default — see `missing_inputs` on every recommendation.

**Signal**: ≥75 `STRONG_ACCUMULATE` · 60–74 `ACCUMULATE` · 45–59 `WATCH` · 30–44 `AVOID` · <30 `STRONG_AVOID`

**Levels** (`app/services/levels.py`): ATR(14), entry zone = `close ± 0.5×ATR`, stop = `max(close − 2×ATR, 20-day rolling low)` whichever is tighter (stored as `stop_method`), target set to hit exactly a 1:2 risk:reward off whichever stop wins.

**Position sizing**: risk per trade `0.5% / 1% / 2%` of capital by low/moderate/high appetite; `shares = floor(max_loss / (entry − stop))`, capped by a per-stock allocation limit (`15% / 20% / 25%` of capital).

**Portfolio construction**: candidates filtered to overall_score ≥ `70 / 60 / 50` by appetite, ranked by score, greedily allocated respecting a 2-stocks-per-sector cap and a `20% / 10% / 5%` cash buffer.

## Backtest — results as measured

One example run over the seeded 40-stock universe, exactly as returned by `POST /backtest` — not tuned, not cherry-picked:

**Config**: `start_date=2025-03-01, end_date=2026-08-01, initial_capital=₹500,000, risk_appetite=moderate, rebalance_frequency=monthly, horizon_days=90, transaction_cost_pct=0.12% (round-trip), slippage_pct=0.05%, risk_free_rate=6.5%`

| | Strategy | NIFTY 50 buy & hold |
|---|---|---|
| Total return | 4.22% | 10.24% |
| CAGR | 2.97% | 7.16% |
| Sharpe | -0.288 | 0.110 |
| Sortino | -0.366 | 0.163 |
| Max drawdown | -9.14% (132 days) | -15.18% (140 days) |
| Final equity | ₹521,076 | ₹551,184 |

Strategy-only: 88 trades, 38.64% win rate, avg win ₹4,933 / avg loss -₹2,720, profit factor 1.14, avg holding period 26.9 days.

The strategy underperformed the benchmark on raw return in this window but took on less drawdown risk to do it — reported plainly either way. **Backtest scoring uses only the technical + risk sub-scores** (renormalized 0.6/0.4), not the full four-score composite: `fundamentals` holds one snapshot per stock, not a historical time series, so applying it to a rebalance date in the past would be look-ahead bias. This means backtest results measure a price-action strategy, not the fundamentals-aware strategy used for live recommendations.

## ML results — as measured

Full writeup: [`docs/ml_results.md`](docs/ml_results.md). Headline, from a real training run on the seeded universe:

- 1,100 usable (stock, date) rows survive after dropping missing features (mostly the ROE/debt-equity gaps below)
- Time-based 70/15/15 split, no shuffling
- **LogisticRegression beat RandomForest** on every held-out test metric (ROC-AUC 0.734 vs 0.566) — so `app/ml/predict.py` serves the LR model, not RF, despite RF being the more complex "expected" choice. Reported and used as measured, not tuned until RF won.
- On the test period, the ML probability ranking beat the rule-based technical+risk score at identifying near-term outperformers (63.3% vs 34.7% precision at the top 30%, vs. a 50.9% base rate) — one 165-row test window, not a general claim.
- `ml_probability` is an **additional** field on recommendations, never folded into `overall_score`.

## Limitations

- **yfinance data quality**: fundamentals (`roe`, `roce`, `debt_to_equity`, etc.) are frequently missing for Indian tickers — `roce` has *no* yfinance equivalent and is always NULL. Occasional single-day price gaps occur (a stock's most recent close can be NULL); every downstream computation (indicators, scores, levels) correctly propagates that as NULL rather than fabricating a value.
- **Fundamentals are a single snapshot**, not a time series — this is why backtesting and the ML feature set both have documented workarounds/exclusions rather than pretending to have historical fundamentals.
- **Corporate actions**: symbols can change (e.g. demergers) — the seed universe was corrected for three such cases (Tata Motors → TMCV, Vedanta → VEDL, LTIM unresolved on Yahoo) found via live verification, not assumed.
- **Not implemented**: real-money execution (by design), shorting in paper trading (long-only), short-selling generally, options/derivatives, intraday data (daily bars only), realized P&L for closed portfolio holdings (schema has no `exit_price` column — only paper trading tracks that).
- **Backtest and paper trading transaction costs** are a flat assumption (0.12% round-trip), not a real broker's actual fee schedule.
- Nothing in this repository is investment advice. All scores, backtests, and ML outputs are descriptive analysis of historical data, not predictions or recommendations to trade.
