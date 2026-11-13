"""Phase 28, source 5 — NIFTY 50 / NIFTY Next 50 / NIFTY 500 exclusions from
NSE Indices press releases (niftyindices.com).

Stage 1 (network): list every press release, keep the ones whose title can
announce a constituent change, download each PDF once (ALTDATA_DIR/index_pdf/)
and extract its text with the standard library (ALTDATA_DIR/index_text/).
No PDF library is in the approved stack; the releases use TrueType fonts with
single-byte strings, which zlib + the PDF text operators are enough to read.

Stage 2 (parse): find each NIFTY 50 / NIFTY Next 50 / NIFTY 500 section, its
"excluded" list and the heading of the part of the release it sits in. A
symbol is kept only if NSE's bhavcopy confirms it traded in the week before
the effective date; anything unconfirmed is counted, not guessed at.

    python scripts/phase28_fetch_index_changes.py
"""

import io
import json
import os
import re
import sys
import zipfile
import zlib
from datetime import date, datetime, timedelta
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))

from phase28_http import ROOT, UA, pause  # noqa: E402

BASE = "https://www.niftyindices.com"
FIRST, LAST = date(2017, 10, 1), date(2026, 10, 31)
TITLE = re.compile(r"replac|inclusion|exclusion|change|revision in criteria|reconstitut|index maintenance", re.I)
NOT_EQUITY = re.compile(r"fixed income|bond|g-sec|gilt|\bsdl\b|t-bill|treasury|debt|liquid", re.I)
BHAV = Path(os.environ.get("NSE_ARCHIVE_DIR", Path.home() / ".cache/equitylens/nse")).expanduser() / "bhavcopy"
INDEX_NAMES = {"50": "NIFTY 50", "next50": "NIFTY Next 50", "500": "NIFTY 500"}
CORPORATE = re.compile(r"amalgamat|merger|scheme|demerg|delist|suspen|acquisition|takeover|open offer", re.I)


# ---------------------------------------------------------------- stage 1 --

def press_items() -> list[dict]:
    html = requests.get(f"{BASE}/press-release", headers={"User-Agent": UA}, timeout=60).text
    items = []
    for d, href, title in re.findall(r'data-date="([^"]+)".*?<a href=.([^\'"]+\.pdf).[^>]*>([^<]+)</a>', html, re.S):
        when = datetime.strptime(d.strip(), "%b %d, %Y").date()
        title = re.sub(r"\s+", " ", title.replace("&amp;", "&")).strip()
        if FIRST <= when <= LAST and TITLE.search(title) and not NOT_EQUITY.search(title):
            items.append({"date": when.isoformat(), "href": href, "title": title})
    return items


def _streams(pdf: bytes):
    for m in re.finditer(rb"<<(.*?)>>\s*stream\r?\n", pdf, re.S):
        raw = pdf[m.end():pdf.find(b"endstream", m.end())]
        if b"FlateDecode" in m.group(1):
            try:
                yield zlib.decompressobj().decompress(raw)
            except zlib.error:
                continue
        else:
            yield raw


def _unescape(s: bytes) -> str:
    s = re.sub(rb"\\([nrtbf()\\])", lambda m: {b"n": b" ", b"r": b" ", b"t": b" ", b"b": b"", b"f": b""}.get(
        m.group(1), m.group(1)), s)
    s = re.sub(rb"\\(\d{1,3})", lambda m: bytes([int(m.group(1), 8) & 0xFF]), s)
    return s.decode("latin-1")


def pdf_text(pdf: bytes) -> str:
    out = []
    for content in _streams(pdf):
        if b"BT" not in content:
            continue
        for m in re.finditer(rb"\[(.*?)\]\s*TJ|\((.*?)(?<!\\)\)\s*Tj|(T\*|Td|TD|ET)", content, re.S):
            if m.group(1) is not None:
                out.append("".join(_unescape(p) for p in re.findall(rb"\((.*?)(?<!\\)\)", m.group(1), re.S)))
            elif m.group(2) is not None:
                out.append(_unescape(m.group(2)))
            else:
                out.append(" ")  # a line move or end of text block separates words
    return re.sub(r"\s+", " ", "".join(out)).strip()


def download(items: list[dict]) -> None:
    (ROOT / "index_pdf").mkdir(parents=True, exist_ok=True)
    (ROOT / "index_text").mkdir(parents=True, exist_ok=True)
    for it in items:
        name = Path(it["href"]).name
        pdf_path, txt_path = ROOT / "index_pdf" / name, ROOT / "index_text" / (name[:-4] + ".txt")
        if not pdf_path.exists():
            r = requests.get(BASE + it["href"], headers={"User-Agent": UA}, timeout=90)
            if r.status_code != 200 or not r.content.startswith(b"%PDF"):
                print(f"  not a PDF ({r.status_code}): {it['href']}", flush=True)
                pause()
                continue
            pdf_path.write_bytes(r.content)
            pause()
        if not txt_path.exists():
            txt_path.write_text(pdf_text(pdf_path.read_bytes()))


# ---------------------------------------------------------------- stage 2 --

def bhav_symbols(around: date) -> set[str]:
    """EQ symbols NSE reported in the 7 calendar days before `around`."""
    syms: set[str] = set()
    for k in range(1, 8):
        p = BHAV / f"{around - timedelta(days=k):%Y%m%d}.zip"
        if not p.exists():
            continue
        with zipfile.ZipFile(p) as z:
            for name in z.namelist():
                text = z.read(name).decode("latin-1")
                head = text.split("\n", 1)[0].upper()
                cols = [c.strip() for c in head.split(",")]
                si = cols.index("SYMBOL") if "SYMBOL" in cols else cols.index("TCKRSYMB")
                se = cols.index("SERIES") if "SERIES" in cols else cols.index("SCTYSRS")
                for line in io.StringIO(text.split("\n", 1)[1]):
                    f = line.split(",")
                    if len(f) > max(si, se) and f[se].strip() == "EQ":
                        syms.add(f[si].strip())
    return syms


MONTHS = "January|February|March|April|May|June|July|August|September|October|November|December"


def effective_date(title: str, text: str) -> date | None:
    for source in (title, text):
        compact = re.sub(r"\s+", "", source)
        m = re.search(rf"(?:w\.?e\.?f\.?|effectivefrom)({MONTHS})(\d{{1,2}}),?(\d{{4}})", compact, re.I)
        if m:
            return datetime.strptime(f"{m.group(1)} {m.group(2)} {m.group(3)}", "%B %d %Y").date()
    return None


def parse_release(text: str, valid: set[str]) -> tuple[list[dict], int]:
    """(exclusions, unconfirmed_count). Works on the compact text (all
    whitespace removed) because the extraction splits words unpredictably
    ("Nifty 50 0" is NIFTY 500), mapping positions back to the spaced tokens
    for the symbol lists."""
    tokens = text.split(" ")
    compact, owner = [], []
    for i, t in enumerate(tokens):
        compact.append(t)
        owner.extend([i] * len(t))
    c = "".join(compact)
    lower = c.lower()

    # Part headings: "A) ..." / "A. ..." / "B) ..." up to the first colon.
    parts = [(m.start(), m.group(0)) for m in re.finditer(r"(?<![a-z0-9])[a-h][.)][A-Z][^:]{5,300}:", c)]

    def heading_at(pos: int) -> str:
        before = [h for p, h in parts if p < pos]
        return before[-1] if before else c[:400]

    headers = [(m.start(), m.end(), m.group(1).lower()) for m in re.finditer(
        r"nifty(50|next50|500)(?=thefollowing|:thefollowing|-thefollowing|\(.{0,40}\)thefollowing)", lower)]
    out, unconfirmed = [], 0
    for k, (start, end, key) in enumerate(headers):
        stop = headers[k + 1][0] if k + 1 < len(headers) else len(c)
        seg_c = lower[end:stop]
        m = re.search(r"(?:is|are)beingexcluded", seg_c)
        if not m:
            continue
        ex_start = end + m.end()
        n = re.search(r"(?:is|are)beingincluded|noinclusion|noexclusion", seg_c[m.end():])
        ex_end = ex_start + n.start() if n else stop
        # Spaced tokens of the excluded list; serial numbers split it into rows.
        first, last = owner[min(ex_start, len(owner) - 1)], owner[min(ex_end, len(owner)) - 1]
        span = [t for t in tokens[first:last + 1] if t]
        rows, cur, expect = [], None, 1
        for t in span:
            if t == str(expect):
                if cur is not None:
                    rows.append(cur)
                cur, expect = [], expect + 1
            elif cur is not None:
                cur.append(t)
        if cur:
            rows.append(cur)
        for row in rows:
            sym = None
            # The symbol is the last thing on the row; the extraction may have
            # split it, so try the last one, two and three tokens joined.
            for j in range(len(row) - 1, -1, -1):
                for w in (1, 2, 3):
                    cand = "".join(row[max(0, j - w + 1):j + 1]).strip(".,;:")
                    if cand in valid:
                        sym = cand
                        break
                if sym:
                    break
            if sym is None:
                unconfirmed += 1
                continue
            out.append({"index": INDEX_NAMES[key], "symbol": sym, "heading": heading_at(start)[:300]})
    return out, unconfirmed


def main() -> None:
    items = press_items()
    print(f"{len(items)} candidate releases {FIRST} → {LAST}", flush=True)
    download(items)
    rows, report = [], []
    for it in items:
        name = Path(it["href"]).name
        txt_path = ROOT / "index_text" / (name[:-4] + ".txt")
        if not txt_path.exists():
            report.append({**it, "status": "no pdf"})
            continue
        text = txt_path.read_text()
        eff = effective_date(it["title"], text)
        if eff is None:
            report.append({**it, "status": "no effective date", "chars": len(text)})
            continue
        found, unconfirmed = parse_release(text, bhav_symbols(eff))
        for f in found:
            rows.append({**f, "release": name, "release_date": it["date"], "effective": eff.isoformat(),
                         "periodic_review": "review" in f["heading"].lower() and not CORPORATE.search(f["heading"])})
        report.append({**it, "status": "parsed", "effective": eff.isoformat(), "exclusions": len(found),
                       "unconfirmed": unconfirmed, "chars": len(text)})
    (ROOT / "index_exclusions.json").write_text(json.dumps(rows, indent=1))
    (ROOT / "index_releases.json").write_text(json.dumps(report, indent=1))
    print(f"releases parsed: {sum(r['status'] == 'parsed' for r in report)} of {len(report)}; "
          f"exclusions {len(rows)} ({sum(r['periodic_review'] for r in rows)} periodic-review); "
          f"unconfirmed symbols {sum(r.get('unconfirmed', 0) for r in report)}")


if __name__ == "__main__":
    main()
