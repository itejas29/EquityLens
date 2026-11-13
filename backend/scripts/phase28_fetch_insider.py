"""Phase 28, source 1 — insider and promoter trades (SEBI PIT disclosures) from NSE.

Network only. One request per half-month window, cached as the raw JSON NSE
returned (ALTDATA_DIR/pit/<from>_<to>.json). A window that cannot be fetched
is recorded in the manifest, never silently skipped.

    python scripts/phase28_fetch_insider.py
"""

import json
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from phase28_http import ROOT, NseSession, pause  # noqa: E402

START, END = date(2018, 1, 1), date(2026, 10, 31)
URL = "https://www.nseindia.com/api/corporates-pit?index=equities&from_date={a}&to_date={b}"
REFERER = "https://www.nseindia.com/companies-listing/corporate-filings-insider-trading"


def windows():
    d = START
    while d <= END:
        mid = d.replace(day=15)
        nxt = (d.replace(day=28) + timedelta(days=4)).replace(day=1)
        for a, b in ((d, mid), (mid + timedelta(days=1), nxt - timedelta(days=1))):
            yield a, min(b, END)
        d = nxt


def main() -> None:
    out = ROOT / "pit"
    out.mkdir(parents=True, exist_ok=True)
    manifest_path = ROOT / "pit_manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    nse = NseSession(REFERER)
    for a, b in windows():
        key = f"{a:%Y%m%d}_{b:%Y%m%d}"
        if (out / f"{key}.json").exists():
            continue
        j = nse.get_json(URL.format(a=f"{a:%d-%m-%Y}", b=f"{b:%d-%m-%Y}"))
        if j is None:
            manifest[key] = "failed"
        else:
            rows = j.get("data", []) if isinstance(j, dict) else j
            (out / f"{key}.json").write_text(json.dumps(rows))
            manifest[key] = len(rows)
            print(f"{key}: {len(rows)} records", flush=True)
        manifest_path.write_text(json.dumps(manifest, indent=0, sort_keys=True))
        pause()
    failed = [k for k, v in manifest.items() if v == "failed"]
    print(f"done: {len(manifest)} windows, {sum(v for v in manifest.values() if isinstance(v, int))} records, "
          f"{len(failed)} failed {failed[:10]}")


if __name__ == "__main__":
    main()
