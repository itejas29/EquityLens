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
    capped = [k for k, v in manifest.items() if isinstance(v, str) and "cap" in v]
    failed = [k for k, v in manifest.items() if v == "failed"]
    print(f"done: {len(manifest)} requests, {len(capped)} days at the cap, {len(failed)} failed {failed[:10]}")


if __name__ == "__main__":
    main()
