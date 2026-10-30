# EquityLens

A research and paper-trading platform for NSE (Indian stock exchange) equities: scores a ~500-stock NSE universe across four dimensions (technical, fundamental, valuation, risk), builds risk-sized portfolios, backtests the strategy point-in-time, trains a small secondary ML signal, and lets you paper-trade against real prices.

**This is a research tool, not investment advice.** No real-money execution. Past performance does not indicate future returns.

## What the research found

The live strategy, V1, was measured until the measurement could be trusted, and
then it showed no edge. V1 is 12-month-minus-1-month momentum: 10 stocks,
rebalanced monthly, behind a 200-day market regime filter.

- **The engine was audited first.** Two execution defects were fixed:
  - stops that gapped down filled at a price the stock never traded at (#19);
  - entries filled at the same close the signal was computed from (#20).

  Fixing them *raised* V1's backtest on the same data, from −0.46 to +3.65
  percentage points per 6-month fold against NIFTY 50. The old engine had been
  buying half an ATR above the signal close.
- **Survivorship removed the edge**
  ([Phase 24](docs/experiments/phase24_survivorship/FINDINGS.md)). Each fold's
  universe was rebuilt from NSE's own daily bhavcopies: 3,974,703 rows over
  2,510 trading days, including 71 companies no longer listed.
  - On today's survivors, V1 beat NIFTY by +3.04pp a fold (median +2.40).
  - On the universe as it actually was, it beat NIFTY by +0.04pp a fold
    (median −0.03).
- **Published alternatives did no better**
  ([Phase 25](docs/experiments/phase25_literature_candidates/FINDINGS.md)). Four
  alternatives were each pre-registered and run on the survivorship-free
  universe: residual momentum, nearness to the 52-week high, momentum plus low
  volatility, and a gradient-boosted ML ranker.
  - Every one had a negative median excess over NIFTY 50.
  - The ML ranker was the weakest.
  - All of them trailed an equal-weight basket of the same 500 stocks.
- **The live record agrees**
  ([forward record](docs/experiments/forward_exit_diagnosis/FINDINGS.md)). The
  published calls were scored the way a follower would have traded them: limit
  fills, 0.24% costs, one position per stock. Over 20 sessions they did no
  better than an equal-weight basket of their universe (−0.04%).
- **The one rule with an effect is a crash brake, and it is insurance**
  ([Phase 26](docs/experiments/phase26_crash_brake/FINDINGS.md)). Holding NIFTY
  only while it was above its 200-day average, 2008–2026:
  - cut the worst loss from −45.4% to −25.5%;
  - cut the annual return from 10.0% to 6.0%.

The dashboard serves a strategy verdict generated from the Phase 24 backtest and
the live record: Measurement PASS, Edge FAIL, Promotion BLOCKED
([audit closure](docs/audit/momentum_v1.0/momentum_v1.0_audit_closure.md)).
Every experiment from Phase 23 on had its rules committed before it ran. Any
later change is logged in its pre-registration.

## What it does

- Pulls NSE price history and fundamentals from Yahoo Finance (`yfinance`)
- Computes technical indicators from formula (SMA, Wilder RSI, MACD, ATR, rolling volatility/beta/drawdown) — no `ta-lib`, no `pandas-ta`
- Scores every stock 0–100 on four sub-scores (technical / fundamental / valuation / risk), each with documented normalization and missing-data handling, combined into a weighted composite with a signal label
- Computes ATR-based entry zone / stop-loss / target for each stock
- Builds a capital-sized, sector-capped, risk-appetite-aware portfolio from the scored universe
- Backtests that strategy point-in-time on the data (every rebalance scores only rows dated on or before that day), with transaction costs and slippage, against a NIFTY 50 buy-and-hold benchmark. Universe **membership** is not point-in-time by default — see Limitations
- Runs pre-registered research phases against a local lab database: walk-forward folds, survivorship-free universes rebuilt from NSE bhavcopies, and a verdict generator that grades the strategy from test outcomes (`docs/experiments/`, `docs/audit/`)
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

Everything that writes, fetches from upstream, or spends real compute requires
a token. Read endpoints do not. `/auth/login` and `/auth/register` are limited
to 20 requests/minute per IP; the analysis endpoints to 100/minute per user
(per IP when anonymous). The table covers the core endpoints; FastAPI's `/docs`
page lists every route.

| Method | Path | Notes |
|---|---|---|
| GET | `/ping` | liveness only, no DB — for cold-start probes |
| GET | `/health` | DB + Redis connectivity check |
| GET | `/health/pipeline` * | coverage, signal freshness, scheduler heartbeats |
| POST | `/auth/register` | |
| POST | `/auth/login` | |
| GET | `/auth/me` * | |
| GET | `/stocks` | paginated, `?sector=` filter |
| GET | `/stocks/search` | `?q=&limit=`, searches all NSE listings |
| POST | `/stocks/{symbol}/ingest` * | on-demand fetch + index, rate-limited |
| POST | `/stocks/catalogue/refresh` * | reload the NSE equity list, rate-limited |
| GET | `/stocks/{symbol}` | cached 15m |
| GET | `/stocks/{symbol}/prices` | `?from=&to=`, never cached |
| POST | `/stocks/{symbol}/refresh` * | re-fetch from yfinance, rate-limited, invalidates caches |
| POST | `/stocks/{symbol}/compute-indicators` * | rate-limited |
| GET | `/stocks/{symbol}/indicators` | `?from=&to=` |
| GET | `/daily-signals` | `?date=`, defaults to most recent published shortlist |
| GET | `/daily-signals/dates` | dates with a published shortlist |
| POST | `/daily-signals/run` * | manual trigger for the 09:15 IST job, rate-limited |
| GET | `/daily-signals/track-record/realistic` | the published calls scored as a follower would have traded them |
| GET | `/strategy-verdict` | the current strategy verdict, served from a committed artifact |
| POST | `/stocks/{symbol}/score` | rate-limited |
| POST | `/scoring/run-universe` * | rate-limited, invalidates scored-universe cache |
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
erodes the reward the target was set for. This is a private tool for a known
group, not a published research product; the decisive wording does not come
with any claim of accuracy, and the research below finds no edge over NIFTY 50.

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

**Levels** (`app/services/levels.py`): ATR(14), entry zone = `close ± 0.5×ATR`, target set from a nominal 1:2 risk:reward off the stop.

Two things about this are easy to misread:

- **The stop rule differs between the two paths.** The `recommendations` view uses the defaults — `max(close − 2×ATR, 20-day rolling low)`, whichever is tighter, stored as `stop_method`. The **live V1 strategy that publishes daily signals uses a 4×ATR stop and no support stop at all**, so its `stop_method` is always `atr`.
- **The reported `risk_reward` is the realised ratio, not the nominal 1:2.** The target is built off the *close*, while the entry zone extends half an ATR *above* it — so a buyer filling at the top of the published zone gets less. Measured across 60 live stocks: **1.67**, not 2.00. On the `recommendations` defaults, where a support stop can sit above the ATR stop, a worked case gives **0.50**. `risk_reward_nominal` carries the 2.0 parameter separately.

**Position sizing**: risk per trade `0.5% / 1% / 2%` of capital by low/moderate/high appetite; `shares = floor(max_loss / (entry − stop))`, capped by a per-stock allocation limit (`15% / 20% / 25%` of capital).

**Portfolio construction**: candidates filtered to overall_score ≥ `70 / 60 / 50` by appetite, ranked by score, greedily allocated respecting a 2-stocks-per-sector cap and a `20% / 10% / 5%` cash buffer.

## Live track record — results as measured

Forward and out-of-sample. Measured 2026-10-30 on a read-only export of
production: 296 calls published on 37 dates (2026-08-14 → 2026-10-30), covering
59 stocks. `GET /daily-signals/track-record/realistic` and the Track Record page
compute the same record live.

The calls are scored the way someone following them could have traded:
- **Fill:** a limit order at the top of the entry zone. It fills at the open if
  the stock opened at or below that level, at the level if the day traded down
  to it, and otherwise counts as a miss.
- **Costs:** 0.24% round trip.
- **One position per stock:** a repeat call on a stock already held adds
  nothing. The 296 calls became 66 positions in 45 stocks.
- **Exit:** the stop or the target, whichever is touched first, within 20
  sessions.
- **Clean days only:** 10 dates are scored separately. Their shortlists were
  built from their own session's close, published after 15:30, or dated on a
  closed day.

| Sessions | n | Mean net | Win rate | vs NIFTY 50 | vs NIFTY200 Momentum 30 (MOM30IETF) | vs equal-weight universe |
|---|---|---|---|---|---|---|
| 1 | 60 | −0.46% | 58.3% | −0.51 | −1.10 | −1.16 |
| 5 | 43 | −0.23% | 39.5% | +0.48 | +0.15 | −1.10 |
| 10 | 43 | +0.86% | 53.5% | +2.46 | +2.55 | +0.41 |
| 20 | 37 | −0.02% | 45.9% | +3.40 | +3.73 | −0.04 |

**The last column answers "does the selection work".** The picks beat the
large-cap indices only because they were smaller stocks, and smaller stocks as
a whole fell less in these weeks. An equal-weight basket of the whole universe
did as well: over 20 sessions, −0.04% (95% interval −3.19 to +3.11).

Stops were hit at the rate a drift-free price path predicts: 5 observed against
5.8 expected. Six entry weeks in one falling market cannot establish or rule out
an edge on their own. Full write-up:
[`docs/experiments/forward_exit_diagnosis/FINDINGS.md`](docs/experiments/forward_exit_diagnosis/FINDINGS.md).

## Backtest research — results as measured

**Method:**
- **Folds:** 16 walk-forward folds, each a 6-month test window, 2018-04 →
  2026-03.
- **Strategy:** V1 is frozen
  ([`backend/app/core/v1_strategy.py`](backend/app/core/v1_strategy.py)).
- **Costs and timing:** 0.12% round trip plus 0.05% slippage per fill. A
  rebalance decided at a close trades in the next session.
- **Benchmark:** the NIFTY 50 price index. It leaves out dividends (roughly
  1–1.5% a year), which flatters every strategy compared with it.
- **Universe, from Phase 24 on:** each fold's top 500 NSE company shares by
  traded value before the fold, delisted companies included, rebuilt from NSE's
  own bhavcopies.

Excess return over NIFTY 50, in percentage points per 6-month fold:

| Phase | Universe | Ranking | Mean | Median | Folds beaten |
|---|---|---|---|---|---|
| 23, old engine | today's survivors | V1, 10 stocks | −0.46 | −1.03 | 8 of 16 |
| 23, fixed engine | today's survivors | V1, 10 stocks | +3.65 | +3.10 | 9 of 16 |
| 24 | today's survivors | V1, 10 stocks | +3.04 | +2.40 | 10 of 16 |
| 24 | **survivorship-free** | V1, 10 stocks | **+0.04** | **−0.03** | 8 of 16 |
| 25 | survivorship-free | V1's ranking, 30 stocks | +0.40 | −0.63 | 8 of 16 |
| 25 | survivorship-free | residual momentum | +0.02 | −1.14 | 5 of 16 |
| 25 | survivorship-free | nearness to 52-week high | −1.12 | −1.86 | 5 of 16 |
| 25 | survivorship-free | momentum + low volatility | −0.42 | −1.20 | 7 of 16 |
| 25 | survivorship-free | ML ranker (gradient-boosted trees) | −1.62 | −2.05 | 5 of 16 |
| 25 | survivorship-free | *equal-weight basket, no ranking* | *+1.84* | *+2.28* | *9 of 16* |

**Reading the table:**
- **The two survivors-only rows differ because of how they were ranked:**
  Phase 23 ranked its universe by Yahoo's traded value, Phase 24 by NSE's.
- **Phase 24's comparison is like for like:** the same ranking, with and without
  delisted companies. It puts the whole of V1's backtest excess down to
  survivorship, +3.00pp a fold.
- **Phase 25 found no candidate:** it tested four published alternatives
  against a pre-registered rule, and none qualified.

**What does have an effect is V1's regime filter**, the rule that cuts exposure
when NIFTY falls below its 200-day average. In the COVID fold, NIFTY fell 38% at
its worst, while no Phase 25 ranking fell more than 6–9%. In rallies the same
rule gave back about as much.

Phase 26 tested the rule on NIFTY itself, 2008-07 → 2026-10, at 0.10% per trade
with cash earning nothing:

| | Return a year | Worst loss | ₹10 lakh became, after tax | Switches a year |
|---|---|---|---|---|
| Buy and hold NIFTY 50 | 10.0% | −45.4% | ₹51.5 lakh | 0 |
| 200-day rule (the registered test) | 6.0% | −25.5% | ₹25.8 lakh | 7.9 |
| 10-month rule | 6.9% | −29.0% | ₹29.5 lakh | 2.0 |
| V1's rule (25% held below the average) | 7.0% | −24.4% | ₹30.1 lakh | 7.9 |

The 200-day rule passed its registered test narrowly. It cut the worst loss
roughly in half and earned slightly more return per unit of that loss, in both
halves of the period. But it fails at 0.25% per trade, and cash would have had
to earn 13.5% a year for it to match buy-and-hold's return.

It worked in sudden crashes: 0% against −45% in 2008, and −6% against −38% in
2020. It saved little in the slow declines of 2015–16 and 2024–25. It is
insurance, not a way to beat NIFTY.

`POST /backtest` still runs on today's universe
(`BacktestConfig.include_inactive` is off by default), so its results carry the
survivorship bias measured above. Its backtest scoring uses only the technical
and risk sub-scores: `fundamentals` holds one snapshot per stock, and applying
it to past rebalance dates would be look-ahead.

## ML results — as measured

**Nothing is served today, and that is the intended behaviour.** The latest
real training run (2026-08-15, 80,846 rows across 375 stocks) measured:

| model | test ROC-AUC | test precision | test accuracy |
|---|---|---|---|
| RandomForest *(selected)* | **0.5369** | 0.6135 | 0.5120 |
| LogisticRegression | 0.5308 | 0.5945 | 0.5528 |

Both are statistically indistinguishable from a coin flip.
`app/ml/predict.py` refuses to serve a model whose own recorded test ROC-AUC is
below `MIN_SERVABLE_ROC_AUC = 0.55`, so `ml_probability` reads as **null** on
every recommendation. Verified against production on 2026-09-10: the serving
gate returns no model. Rendering a near-random number next to a buy call would
make it look like corroborating evidence when it carries no measured
information.

The gate is self-enforcing: a future retrain that genuinely clears the bar
starts serving with no code change.

> **Even 0.5369 is optimistic — it predates two leakage fixes made on
> 2026-09-10, and has not been regenerated since.**
>
> 1. **The split leaked.** It was positional on a stock-by-date panel, so it cut
>    mid-date and put the same trading day on both sides of a boundary; and it
>    did not purge, so the last 20 trading days of each split were labelled by
>    prices inside the next one — the target is a forward 20-day return. It now
>    cuts on date boundaries with a 20-day purge either side.
> 2. **Six fundamentals features were a decade of look-ahead.** `fundamentals`
>    holds snapshots only from 2026-08-14 to 2026-09-01, and the loader took the
>    most recent and broadcast it across rows dated back to 2016-08-16. Constant
>    per stock, it acted as a stock-identity label carrying end-state
>    information. Now excluded by default — point-in-time fundamentals cannot be
>    built from this data, so per build rule 1 they are left out and said so.
>
> See [`docs/audit/ml-leakage.md`](docs/audit/ml-leakage.md).

An earlier version of this section reported ROC-AUC 0.734 and 63.3% precision.
Those came from a 1,100-row run over the original 40-stock seed universe and
were superseded by the 80,846-row run above — which the code had already acted
on by refusing to serve the model. Kept here as a note rather than deleted,
because the gap between the two is the point: a small-sample result that looked
strong did not survive a real dataset.

- `ml_probability` is an **additional** field on recommendations, never folded
  into `overall_score`. Full writeup: [`docs/ml_results.md`](docs/ml_results.md).

**A redesigned ranker did no better**
([Phase 25](docs/experiments/phase25_literature_candidates/FINDINGS.md)). It
changed what the old model got wrong:
- **Target:** each stock's rank within its universe over the next month. The old
  target, "beats NIFTY over 20 days", is mostly a market forecast.
- **Features:** 13 price and volume features, with no look-ahead fundamentals.
- **Training:** retrained at each fold on what preceded it, on the
  survivorship-free universe.

Judged by the portfolio it produced, it was the weakest of five rankings: median
−2.05pp a fold against NIFTY 50. That is consistent with Avramov, Cheng &
Metzker (2023) for US stocks: once microcaps and distressed firms are excluded
and trading costs counted, most of machine learning's stock-picking profit
disappears.

## Tests

`pytest` collects 447 tests. 446 run against SQLite in memory; the row-lock
test needs a real Postgres and is skipped unless `AUDIT_POSTGRES_URL` points at
one. Measured 2026-10-31: 446 passed, 1 skipped. Run them with:

```bash
cd backend && pip install -r requirements-dev.txt && pytest
```

`requirements-dev.txt` is separate from `requirements.txt` on purpose — the
Dockerfile installs the latter into the production image, and a test runner has
no business shipping to the box that trades. CI (`.github/workflows/backend-tests.yml`)
runs the suite, applies the full Alembic chain to an empty database, and builds
the production image on every push and pull request. Deploys stay manual.

## Limitations

- **yfinance data quality**: fundamentals (`roe`, `roce`, `debt_to_equity`, etc.) are frequently missing for Indian tickers — `roce` has *no* yfinance equivalent and is always NULL. Occasional single-day price gaps occur (a stock's most recent close can be NULL); every downstream computation (indicators, scores, levels) correctly propagates that as NULL rather than fabricating a value.
- **Fundamentals are a single snapshot**, not a time series — this is why backtesting and the ML feature set both have documented workarounds/exclusions rather than pretending to have historical fundamentals.
- **Corporate actions**: symbols can change (e.g. demergers) — the seed universe was corrected for three such cases (Tata Motors → TMCV, Vedanta → VEDL, LTIM unresolved on Yahoo) found via live verification, not assumed. Separately, unadjusted splits leave a step in the stored price series: nine were found in production on 2026-09-10, and stocks carrying one inside the momentum lookback are excluded from ranking rather than scored — see [docs/audit/price-series-discontinuities.md](docs/audit/price-series-discontinuities.md).
- **Survivorship bias in the API's default backtest.** `POST /backtest` uses the stocks in the universe *today*, projected backwards; `BacktestConfig.include_inactive` turns that off. Phase 24 measured the cost on V1: a survivors-only universe overstates the excess over NIFTY 50 by +3.00pp per 6-month fold. Research from Phase 24 on uses survivorship-free universes rebuilt from NSE's bhavcopies. Even those are a lower bound on losses, because a delisted position exits at its last traded price. See [docs/audit/survivorship-bias.md](docs/audit/survivorship-bias.md) and [Phase 24](docs/experiments/phase24_survivorship/FINDINGS.md).
- **Benchmarks exclude dividends.** NIFTY 50 here is Yahoo's price index (`^NSEI`). Leaving out roughly 1–1.5% a year of dividends flatters every strategy compared with it.
- **Not implemented**: real-money execution (by design), shorting in paper trading (long-only), short-selling generally, options/derivatives, intraday data (daily bars only), realized P&L for closed portfolio holdings (schema has no `exit_price` column — only paper trading tracks that).
- **Backtest and paper trading transaction costs** are a flat assumption (0.12% round-trip), not a real broker's actual fee schedule.
- Nothing in this repository is investment advice. All scores, backtests, and ML outputs are descriptive analysis of historical data, not predictions or recommendations to trade.
