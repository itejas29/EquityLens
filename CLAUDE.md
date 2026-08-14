# EquityLens
Stock analysis and portfolio decision-support platform for Indian equities (NSE).
Research and paper-trading tool — no real-money execution, no guaranteed returns.

## Stack (fixed)
- Backend: FastAPI, SQLAlchemy 2.0, Pydantic v2, Alembic
- DB: PostgreSQL
- Cache: Redis
- Market data: yfinance (NSE symbols use `.NS` suffix)
- Analysis: pandas, numpy
- ML: scikit-learn
- Frontend: React + Vite
- Deploy: Docker Compose

## Out of scope — do not add
Kafka, microservices, LangChain/LLM agents, deep learning, real-time streaming,
real-money order execution, ta-lib / pandas-ta.

## Build rules
1. No mock data, stub functions, or placeholder logic. If something can't be
   built properly, leave it out and say so.
2. No libraries beyond the stack above without asking first.
3. Money uses DECIMAL, never float.
4. README claims only what has actually been measured. No invented benchmarks.
5. Missing data stays NULL — never backfill with defaults or synthetic values.
6. All user-facing output labelled as analysis, not investment advice.
7. Comment non-obvious decisions (thresholds, formulas, ordering).

## Conventions
- Layout: `app/models/`, `app/schemas/`, `app/services/`, `app/api/v1/`, `app/core/`
- All endpoints under `/api/v1`
- Pydantic schemas for every request/response
- Env config via pydantic-settings, `.env.example` kept current
