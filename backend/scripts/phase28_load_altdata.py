"""Phase 28 — load the cached alternative data into the research database.

Lab only (require_lab_database): everything goes into a separate `altdata`
schema, so nothing touches the app's own tables or its Alembic history. Each
table is rebuilt from the cache on every run. Rupee amounts are NUMERIC, as
the sources print them; nothing is filled in where a source left a blank.

    DATABASE_URL=<lab> python scripts/phase28_load_altdata.py
"""

import json
import sys
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from sqlalchemy import text  # noqa: E402

from app.core.database import SessionLocal  # noqa: E402
from app.core.lab_guard import require_lab_database  # noqa: E402
from phase28_http import ROOT  # noqa: E402

DDL = """
CREATE SCHEMA IF NOT EXISTS altdata;
DROP TABLE IF EXISTS altdata.insider_pit, altdata.deals, altdata.fpi_daily, altdata.index_exclusions;
CREATE TABLE altdata.insider_pit (
    id SERIAL PRIMARY KEY, nse_did TEXT, symbol TEXT NOT NULL, company TEXT, person TEXT, category TEXT,
    mode TEXT, txn_type TEXT, sec_type TEXT, quantity NUMERIC(20, 0), value_inr NUMERIC(20, 2),
    acq_from DATE, acq_to DATE, intimated DATE, broadcast_at TIMESTAMP);
CREATE TABLE altdata.deals (
    id SERIAL PRIMARY KEY, kind TEXT NOT NULL, deal_date DATE NOT NULL, symbol TEXT NOT NULL, client TEXT,
    side TEXT, quantity NUMERIC(20, 0), price NUMERIC(14, 4));
CREATE TABLE altdata.fpi_daily (
    reporting_date DATE PRIMARY KEY, gross_purchases_cr NUMERIC(14, 2), gross_sales_cr NUMERIC(14, 2),
    net_cr NUMERIC(14, 2));
CREATE TABLE altdata.index_exclusions (
    id SERIAL PRIMARY KEY, release TEXT, release_date DATE, effective DATE, index_name TEXT, symbol TEXT,
    heading TEXT, periodic_review BOOLEAN);
"""


def _dec(v) -> Decimal | None:
    if v in (None, "", "-", "NA", "Nil"):
        return None
    try:
        return Decimal(str(v).replace(",", "").strip())
    except InvalidOperation:
        return None


def _date(v: str | None, fmt: str):
    try:
        return datetime.strptime(v.strip(), fmt).date() if v and v.strip() not in ("-", "NA") else None
    except ValueError:
        return None


def main() -> None:
    db = SessionLocal()
    require_lab_database(db.get_bind().url)
    for stmt in filter(None, (s.strip() for s in DDL.split(";"))):
        db.execute(text(stmt))

    pit = []
    for f in sorted((ROOT / "pit").glob("*.json")):
        for r in json.loads(f.read_text()):
            when = None
            if r.get("date"):
                try:
                    when = datetime.strptime(r["date"].strip(), "%d-%b-%Y %H:%M")
                except ValueError:
                    when = None
            pit.append({"did": r.get("did"), "symbol": (r.get("symbol") or "").strip(), "company": r.get("company"),
                        "person": (r.get("acqName") or "").strip(), "category": r.get("personCategory"),
                        "mode": r.get("acqMode"), "txn": r.get("tdpTransactionType"), "sec": r.get("secType"),
                        "qty": _dec(r.get("secAcq")), "val": _dec(r.get("secVal")),
                        "from": _date(r.get("acqfromDt"), "%d-%b-%Y"), "to": _date(r.get("acqtoDt"), "%d-%b-%Y"),
                        "intim": _date(r.get("intimDt"), "%d-%b-%Y"), "at": when})
    db.execute(text("""INSERT INTO altdata.insider_pit (nse_did, symbol, company, person, category, mode, txn_type,
        sec_type, quantity, value_inr, acq_from, acq_to, intimated, broadcast_at) VALUES (:did, :symbol, :company,
        :person, :category, :mode, :txn, :sec, :qty, :val, :from, :to, :intim, :at)"""), pit)
    print(f"insider_pit: {len(pit)} rows", flush=True)

    deals = []
    for kind in ("bulk", "block"):
        for f in sorted((ROOT / "deals" / kind).glob("*.json")):
            for r in json.loads(f.read_text()):
                deals.append({"kind": kind, "d": _date(r.get("BD_DT_DATE"), "%d-%b-%Y"),
                              "symbol": (r.get("BD_SYMBOL") or "").strip(), "client": (r.get("BD_CLIENT_NAME") or "").strip(),
                              "side": (r.get("BD_BUY_SELL") or "").strip().upper(), "qty": _dec(r.get("BD_QTY_TRD")),
                              "px": _dec(r.get("BD_TP_WATP"))})
    # A month fetched whole and then again day by day (block deals at the cap)
    # appears twice; the deal itself is one.
    seen, unique = set(), []
    for d in deals:
        key = (d["kind"], d["d"], d["symbol"], d["client"], d["side"], d["qty"], d["px"])
        if key not in seen:
            seen.add(key)
            unique.append(d)
    db.execute(text("""INSERT INTO altdata.deals (kind, deal_date, symbol, client, side, quantity, price)
        VALUES (:kind, :d, :symbol, :client, :side, :qty, :px)"""), unique)
    print(f"deals: {len(unique)} rows ({len(deals) - len(unique)} duplicate fetches dropped)", flush=True)

    fpi = json.loads((ROOT / "fpi_daily.json").read_text())
    db.execute(text("""INSERT INTO altdata.fpi_daily VALUES (:d, :gp, :gs, :net)"""), [
        {"d": _date(r["reporting_date"], "%d-%b-%Y"), "gp": _dec(r["gross_purchases_cr"]),
         "gs": _dec(r["gross_sales_cr"]), "net": _dec(r["net_cr"])} for r in fpi])
    print(f"fpi_daily: {len(fpi)} rows", flush=True)

    ex = json.loads((ROOT / "index_exclusions.json").read_text())
    db.execute(text("""INSERT INTO altdata.index_exclusions (release, release_date, effective, index_name, symbol,
        heading, periodic_review) VALUES (:release, :release_date, :effective, :index, :symbol, :heading,
        :periodic_review)"""), ex)
    print(f"index_exclusions: {len(ex)} rows", flush=True)
    db.commit()


if __name__ == "__main__":
    main()
