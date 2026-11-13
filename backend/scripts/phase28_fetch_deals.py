"""Phase 28, source 2 — NSE bulk and block deals.

Network only. NSE's historical endpoint returns at most 70 rows per request,
so bulk deals are fetched one day at a time and block deals (far fewer) one
month at a time, falling back to single days for any month that hits the cap.
A single day that still returns exactly the cap is recorded as possibly
truncated in the manifest. Cached raw: ALTDATA_DIR/deals/<kind>/<key>.json.

    python scripts/phase28_fetch_deals.py
"""

import json
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from phase28_http import ROOT, NseSession, pause  # noqa: E402

START, END = date(2018, 1, 1), date(2026, 10, 31)
URL = ("https://www.nseindia.com/api/historicalOR/bulk-block-short-deals"
       "?optionType={kind}_deals&from={a}&to={b}")
REFERER = "https://www.nseindia.com/report-detail/display-bulk-and-block-deals"
CAP = 70


def _fetch(nse, kind, a, b, out, manifest):
    key = f"{a:%Y%m%d}_{b:%Y%m%d}"
    path = out / f"{key}.json"
    if path.exists():
        return json.loads(path.read_text())
    j = nse.get_json(URL.format(kind=kind, a=f"{a:%d-%m-%Y}", b=f"{b:%d-%m-%Y}"))
    pause()
    if j is None:
        manifest[f"{kind}:{key}"] = "failed"
        return None
    rows = j.get("data", []) if isinstance(j, dict) else j
    path.write_text(json.dumps(rows))
    manifest[f"{kind}:{key}"] = len(rows) if not (a == b and len(rows) >= CAP) else f"{len(rows)} (at cap)"
    return rows


def main() -> None:
    manifest_path = ROOT / "deals_manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    nse = NseSession(REFERER)

    out = ROOT / "deals" / "block"
    out.mkdir(parents=True, exist_ok=True)
    m = START
    while m <= END:
        nxt = (m.replace(day=28) + timedelta(days=4)).replace(day=1)
        rows = _fetch(nse, "block", m, min(nxt - timedelta(days=1), END), out, manifest)
        if rows is not None and len(rows) >= CAP:
            d = m
            while d < nxt and d <= END:
                if d.weekday() < 5:
                    _fetch(nse, "block", d, d, out, manifest)
                d += timedelta(days=1)
        m = nxt
        manifest_path.write_text(json.dumps(manifest, indent=0, sort_keys=True))
    print("block deals done", flush=True)

    out = ROOT / "deals" / "bulk"
    out.mkdir(parents=True, exist_ok=True)
    d, n = START, 0
    while d <= END:
        if d.weekday() < 5:
            _fetch(nse, "bulk", d, d, out, manifest)
            n += 1
            if n % 50 == 0:
                manifest_path.write_text(json.dumps(manifest, indent=0, sort_keys=True))
                print(f"bulk through {d}", flush=True)
        d += timedelta(days=1)
    manifest_path.write_text(json.dumps(manifest, indent=0, sort_keys=True))
    refetch_capped_days_as_csv(nse, manifest, manifest_path)
    capped = [k for k, v in manifest.items() if isinstance(v, str) and "at cap" in v]
    failed = [k for k, v in manifest.items() if v == "failed"]
    print(f"done: {len(manifest)} requests, {len(capped)} days still at the cap, {len(failed)} failed {failed[:10]}")


CSV_FIELDS = {"Date": "BD_DT_DATE", "Symbol": "BD_SYMBOL", "Security Name": "BD_SCRIP_NAME",
              "Client Name": "BD_CLIENT_NAME", "Buy / Sell": "BD_BUY_SELL", "Quantity Traded": "BD_QTY_TRD",
              "Trade Price / Wght. Avg. Price": "BD_TP_WATP", "Remarks": "BD_REMARKS"}


def refetch_capped_days_as_csv(nse, manifest, manifest_path) -> None:
    """The JSON endpoint stops at 70 rows; the same endpoint with csv=true
    (the site's own "Download" button) does not — 28-Dec-2023 gave 70 rows as
    JSON and 106 as CSV. Each single day that hit the cap is fetched again as
    CSV and its file replaced, in the JSON rows' own field names, with the
    Indian digit grouping ("1,93,925") removed from the numbers."""
    import csv
    import io

    from phase28_http import UA

    todo = sorted(k for k, v in manifest.items() if isinstance(v, str) and "at cap" in v)
    print(f"re-fetching {len(todo)} capped days as CSV", flush=True)
    for n, key in enumerate(todo, 1):
        kind, span = key.split(":")
        day = span[:8]
        d = f"{day[6:8]}-{day[4:6]}-{day[:4]}"
        url = URL.format(kind=kind, a=d, b=d) + "&csv=true"
        body = None
        for attempt in range(4):
            try:
                r = nse.s.get(url, headers={"User-Agent": UA, "Referer": nse.referer, "Accept": "*/*"}, timeout=60)
                if r.status_code == 200 and "Symbol" in r.text[:300]:
                    body = r.text.lstrip("﻿")
                    break
            except Exception as exc:  # noqa: BLE001 — any failure is retried, then recorded
                print(f"  {key} attempt {attempt + 1}: {type(exc).__name__}", flush=True)
            pause()
            nse._warm()
        pause()
        if body is None:
            manifest[key] = manifest[key] + " (csv failed)"
            continue
        rows = []
        for rec in csv.DictReader(io.StringIO(body)):
            row = {CSV_FIELDS[k.strip()]: (v or "").strip() for k, v in rec.items() if k and k.strip() in CSV_FIELDS}
            for num in ("BD_QTY_TRD", "BD_TP_WATP"):
                row[num] = row.get(num, "").replace(",", "")
            rows.append(row)
        (ROOT / "deals" / kind / f"{span}.json").write_text(json.dumps(rows))
        manifest[key] = f"{len(rows)} (csv)"
        if n % 50 == 0:
            manifest_path.write_text(json.dumps(manifest, indent=0, sort_keys=True))
            print(f"  csv {n}/{len(todo)}", flush=True)
    manifest_path.write_text(json.dumps(manifest, indent=0, sort_keys=True))


if __name__ == "__main__":
    main()
