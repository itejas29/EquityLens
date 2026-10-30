"""Phase 24 data, stage 1 — download NSE's daily bhavcopies and corporate actions.

Network only; writes nothing to any database. Files are cached, so a re-run
fetches only what is missing. Every weekday from START to END is requested; a
404 is recorded as a day NSE did not trade, and the manifest keeps the status
of every request so a gap is always explainable.

    NSE_ARCHIVE_DIR=~/.cache/equitylens/nse python scripts/phase24_fetch_nse_archive.py
"""

import json
import os
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("DATABASE_URL", "postgresql+psycopg2://unused:unused@localhost/unused")
os.environ.setdefault("REDIS_URL", "redis://localhost:6399/0")
os.environ.setdefault("JWT_SECRET_KEY", "archive-fetch-does-not-use-this-secret")

from app.services.nse_archive import bhavcopy_url  # noqa: E402

START, END = date(2016, 6, 1), date(2026, 8, 1)
ROOT = Path(os.environ.get("NSE_ARCHIVE_DIR", Path.home() / ".cache/equitylens/nse")).expanduser()
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
PAUSE = 0.25  # seconds between requests; NSE's archive throttles bursts


def fetch_bhavcopies() -> None:
    out = ROOT / "bhavcopy"
    out.mkdir(parents=True, exist_ok=True)
    manifest_path = ROOT / "bhavcopy_manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    session = requests.Session()
    d = START
    done = 0
    while d <= END:
        key = d.isoformat()
        if d.weekday() < 5 and manifest.get(key) not in (200, 404):
            status = None
            for attempt in range(4):
                try:
                    r = session.get(bhavcopy_url(d), headers={"User-Agent": UA, "Accept": "*/*"}, timeout=30)
                    status = r.status_code
                    if status == 200:
                        (out / f"{d:%Y%m%d}.zip").write_bytes(r.content)
                    if status in (200, 404):
                        break
                except requests.RequestException:
                    status = "error"
                time.sleep(2 ** attempt)
            manifest[key] = status
            done += 1
            if done % 100 == 0:
                manifest_path.write_text(json.dumps(manifest, indent=0, sort_keys=True))
                print(f"  {key}  ({sum(v == 200 for v in manifest.values())} trading days so far)", flush=True)
            time.sleep(PAUSE)
        d += timedelta(days=1)
    manifest_path.write_text(json.dumps(manifest, indent=0, sort_keys=True))
    bad = {k: v for k, v in manifest.items() if v not in (200, 404)}
    print(f"bhavcopies: {sum(v == 200 for v in manifest.values())} trading days, "
          f"{sum(v == 404 for v in manifest.values())} closed, {len(bad)} unresolved {sorted(bad)[:10]}")


def fetch_corporate_actions() -> None:
    """One request per calendar year from NSE's corporate-actions API, which
    needs the cookies its own web page sets first."""
    headers = {"User-Agent": UA, "Accept": "application/json, text/plain, */*",
               "Referer": "https://www.nseindia.com/companies-listing/corporate-filings-actions"}
    session = requests.Session()
    session.get(headers["Referer"], headers=headers, timeout=30)
    for year in range(START.year, END.year + 1):
        path = ROOT / f"corporate_actions_{year}.json"
        if path.exists():
            continue
        url = ("https://www.nseindia.com/api/corporates-corporateActions?index=equities"
               f"&from_date=01-01-{year}&to_date=31-12-{year}")
        r = session.get(url, headers=headers, timeout=60)
        r.raise_for_status()
        path.write_text(r.text)
        print(f"corporate actions {year}: {len(r.json())} records", flush=True)
        time.sleep(1)


if __name__ == "__main__":
    ROOT.mkdir(parents=True, exist_ok=True)
    print(f"archive dir: {ROOT}")
    fetch_corporate_actions()
    fetch_bhavcopies()
