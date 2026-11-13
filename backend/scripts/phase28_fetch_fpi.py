"""Phase 28, source 4 — daily FPI (FII) flows from NSDL.

Network only. NSDL's archive page returns, for a chosen date, every reporting
date of that month up to it. One postback per month, with the month's last
day, covers the month. The raw HTML is cached (ALTDATA_DIR/fpi/<yyyymm>.html)
and parsed into ALTDATA_DIR/fpi_daily.json: one row per reporting date with
the Equity / Stock Exchange gross purchases, gross sales and net investment,
in ₹ crore as NSDL prints them (parentheses are negatives).

NSE's own FII/DII endpoint only serves the latest day, so it cannot supply
history; NSDL's figures are custodian-confirmed trades up to the previous
session, which is why the reporting date is the public date.

    python scripts/phase28_fetch_fpi.py
"""

import json
import re
import sys
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))

from phase28_http import ROOT, UA, pause  # noqa: E402

START, END = date(2016, 1, 1), date(2026, 10, 31)
URL = "https://www.fpi.nsdl.co.in/web/Reports/Archive.aspx"


def _cells(html: str) -> list[list[str]]:
    out = []
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.S):
        cells = [re.sub(r"<[^>]+>|&nbsp;|\s+", " ", c).strip()
                 for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, re.S)]
        if any(cells):
            out.append(cells)
    return out


def _num(text: str) -> Decimal:
    t = text.replace(",", "").strip()
    neg = t.startswith("(") and t.endswith(")")
    v = Decimal(t.strip("()"))
    return -v if neg else v


def parse(html: str) -> list[dict]:
    """Rows: a reporting date opens a block whose first line is
    [date, 'Equity', 'Stock Exchange', gross purchase, gross sales, net, net USD, rate]."""
    rows = []
    for c in _cells(html):
        if len(c) >= 6 and re.fullmatch(r"\d{2}-[A-Za-z]{3}-\d{4}", c[0]) and c[1] == "Equity" \
                and c[2] == "Stock Exchange":
            rows.append({"reporting_date": c[0], "gross_purchases_cr": str(_num(c[3])),
                         "gross_sales_cr": str(_num(c[4])), "net_cr": str(_num(c[5]))})
    return rows


def fetch_month(s: requests.Session, last: date) -> str:
    page = s.get(URL, headers={"User-Agent": UA}, timeout=60).text
    form = dict(re.findall(r'<input[^>]*name="(__[A-Z]+)"[^>]*value="([^"]*)"', page))
    stamp = f"{last:%d-%b-%Y}"
    form.update({"__EVENTTARGET": "btnSubmit1", "__EVENTARGUMENT": "", "txtDate": stamp, "hdnDate": stamp,
                 "hdnFlag": "", "HdnValexceldata": ""})
    return s.post(URL, data=form, headers={"User-Agent": UA, "Referer": URL}, timeout=90).text


def main() -> None:
    out = ROOT / "fpi"
    out.mkdir(parents=True, exist_ok=True)
    s = requests.Session()
    m = START
    while m <= END:
        nxt = (m.replace(day=28) + timedelta(days=4)).replace(day=1)
        path = out / f"{m:%Y%m}.html"
        if not path.exists():
            for attempt in range(4):
                try:
                    html = fetch_month(s, min(nxt - timedelta(days=1), END))
                    if "Daily Trends in FPI Investments" in html:
                        path.write_text(html)
                        break
                except requests.RequestException as exc:
                    print(f"  {m:%Y-%m} attempt {attempt + 1}: {type(exc).__name__}", flush=True)
                pause()
                s = requests.Session()
            print(f"{m:%Y-%m}: {'ok' if path.exists() else 'FAILED'}", flush=True)
            pause()
        m = nxt

    daily, missing = {}, []
    m = START
    while m <= END:
        path = out / f"{m:%Y%m}.html"
        if path.exists():
            for r in parse(path.read_text()):
                daily[r["reporting_date"]] = r
        else:
            missing.append(f"{m:%Y-%m}")
        m = (m.replace(day=28) + timedelta(days=4)).replace(day=1)
    rows = sorted(daily.values(), key=lambda r: datetime.strptime(r["reporting_date"], "%d-%b-%Y"))
    (ROOT / "fpi_daily.json").write_text(json.dumps(rows, indent=0))
    print(f"done: {len(rows)} reporting dates, months missing: {missing}")


if __name__ == "__main__":
    main()
