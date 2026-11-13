"""Phase 28 — shared HTTP plumbing for the alternative-data downloaders.

Network only; nothing here touches a database. Every downloader caches its raw
responses under ALTDATA_DIR (default ~/.cache/equitylens/altdata), so a re-run
fetches only what is missing and every number in the findings can be traced
back to the bytes the source returned.

NSE's JSON APIs refuse a request without the cookies its home page sets (an
Akamai bot check), so NseSession loads the home page first and again whenever
a request comes back refused. Requests are paced: these are public sites, and
a research job has no reason to hit them faster than a person browsing.
"""

import os
import time
from pathlib import Path

import requests

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
ROOT = Path(os.environ.get("ALTDATA_DIR", Path.home() / ".cache/equitylens/altdata")).expanduser()
PAUSE_SECONDS = 1.0


class NseSession:
    HOME = "https://www.nseindia.com/"

    def __init__(self, referer: str):
        self.referer = referer
        self.s = requests.Session()
        self.calls = 0
        self._warm()

    def _warm(self) -> None:
        self.s = requests.Session()
        self.s.get(self.HOME, headers={"User-Agent": UA, "Accept": "text/html"}, timeout=30)
        time.sleep(PAUSE_SECONDS)

    def get_json(self, url: str):
        """The decoded JSON, or None after repeated refusals. Cookies are
        refreshed every 100 calls and after any refusal — they expire."""
        for attempt in range(5):
            if self.calls and self.calls % 100 == 0:
                self._warm()
            self.calls += 1
            try:
                r = self.s.get(url, headers={"User-Agent": UA, "Accept": "application/json, text/plain, */*",
                                             "Referer": self.referer}, timeout=60)
                if r.status_code == 200 and r.text.strip():
                    return r.json()
                status = r.status_code
            except (requests.RequestException, ValueError) as exc:
                status = type(exc).__name__
            print(f"  refused ({status}) attempt {attempt + 1}: {url}", flush=True)
            time.sleep(5 * (attempt + 1))
            self._warm()
        return None


def pause() -> None:
    time.sleep(PAUSE_SECONDS)
