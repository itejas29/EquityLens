"""NSE's own daily records: bhavcopies and corporate actions.

Used by the survivorship research (scripts/phase24_*), not by production.
Yahoo drops a ticker's history when it is delisted or renamed (HDFC, MINDTREE,
DHFL, CADILAHC all return nothing), so a backtest built only on Yahoo can only
ever contain companies that still exist. NSE's daily bhavcopy lists every
security that traded that day, including the ones that later went bust or
merged — which is what makes a survivorship-free universe possible.

Two archive formats:
  legacy  content/historical/EQUITIES/YYYY/MON/cmDDMONYYYYbhav.csv.zip  (to 2024-07-05)
  UDiFF   content/cm/BhavCopy_NSE_CM_0_0_0_YYYYMMDD_F_0000.csv.zip      (from 2024-07-08)

Bhavcopy prices are NOT adjusted for splits or bonuses (checked: UDiFF's own
previous-close field is unadjusted across HDFC Bank's 2025 bonus). Adjustment
factors come from NSE's corporate-actions list instead, parsed by
`adjustment_factor` — never inferred from price moves, because a genuine 50%
crash looks exactly like a 1:1 bonus, and crashes are what this research is
about.
"""

import csv
import io
import re
import zipfile
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from fractions import Fraction

UDIFF_SINCE = date(2024, 7, 8)


def bhavcopy_url(day: date) -> str:
    if day >= UDIFF_SINCE:
        return f"https://nsearchives.nseindia.com/content/cm/BhavCopy_NSE_CM_0_0_0_{day:%Y%m%d}_F_0000.csv.zip"
    mon = day.strftime("%b").upper()
    return (f"https://nsearchives.nseindia.com/content/historical/EQUITIES/{day.year}/{mon}/"
            f"cm{day:%d}{mon}{day.year}bhav.csv.zip")


@dataclass(frozen=True)
class BhavRow:
    date: date
    symbol: str
    series: str
    isin: str | None
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int
    traded_value: Decimal   # rupees


def _dec(v: str) -> Decimal | None:
    v = (v or "").strip()
    return Decimal(v) if v and v not in ("-", "NA") else None


def parse_bhavcopy(zip_bytes: bytes, day: date) -> list[BhavRow]:
    """Rows of one daily file, both formats, every series (the caller filters).
    A row with any missing price is dropped, not patched."""
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
        name = next(n for n in z.namelist() if n.lower().endswith(".csv"))
        text = z.read(name).decode("utf-8", errors="replace")
    rows: list[BhavRow] = []
    reader = csv.DictReader(io.StringIO(text))
    udiff = "TckrSymb" in (reader.fieldnames or [])
    for r in reader:
        r = {(k or "").strip(): (v or "").strip() for k, v in r.items()}
        if udiff:
            symbol, series, isin = r.get("TckrSymb"), r.get("SctySrs"), r.get("ISIN")
            o, h, l, c = (_dec(r.get(k)) for k in ("OpnPric", "HghPric", "LwPric", "ClsPric"))
            vol, val = r.get("TtlTradgVol"), _dec(r.get("TtlTrfVal"))
        else:
            symbol, series, isin = r.get("SYMBOL"), r.get("SERIES"), r.get("ISIN")
            o, h, l, c = (_dec(r.get(k)) for k in ("OPEN", "HIGH", "LOW", "CLOSE"))
            vol, val = r.get("TOTTRDQTY"), _dec(r.get("TOTTRDVAL"))
        if not symbol or None in (o, h, l, c, val) or not vol:
            continue
        rows.append(BhavRow(day, symbol, series or "", isin or None, o, h, l, c, int(float(vol)), val))
    return rows


# --- corporate actions -------------------------------------------------------

_BONUS = re.compile(r"bonus\s*(\d+)\s*:\s*(\d+)", re.I)
_SPLIT = re.compile(r"(?:split|sub-?division).*?from\s*(?:rs\.?|re\.?|inr)?\s*([\d.]+).*?to\s*(?:rs\.?|re\.?|inr)?\s*([\d.]+)", re.I)


def adjustment_factor(subject: str) -> Fraction | None:
    """Price multiplier for bars BEFORE the ex-date, from NSE's own wording,
    or None if the action does not change the share count.

      "Bonus 1:1"  -> 1 new share for each 1 held: holdings x2, price x1/2
      "Bonus 2:1"  -> 2 new for 1: x3, price x1/3
      "Bonus 1:2"  -> 1 new for 2: x1.5, price x2/3
      "Face Value Split (Sub-Division) - From Rs 10/- Per Share To Rs 2/- Per Share"
                   -> price x2/10

    Rights issues, dividends and the like are not share-count changes here and
    return None — Yahoo's split-adjusted closes, which the rest of the lab
    uses, do not adjust for them either.
    """
    s = subject or ""
    m = _BONUS.search(s)
    if m:
        new, held = int(m.group(1)), int(m.group(2))
        if new > 0 and held > 0:
            return Fraction(held, held + new)
    m = _SPLIT.search(s)
    if m:
        old_fv, new_fv = Fraction(m.group(1)), Fraction(m.group(2))
        if old_fv > 0 and new_fv > 0 and new_fv != old_fv:
            return new_fv / old_fv
    return None


def parse_ex_date(value: str) -> date | None:
    for fmt in ("%d-%b-%Y", "%d-%m-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime((value or "").strip(), fmt).date()
        except ValueError:
            continue
    return None


def adjust_series(bars: list[tuple[date, Decimal, Decimal, Decimal, Decimal, int]],
                  actions: list[tuple[date, Fraction]]) -> list[tuple[date, Decimal, Decimal, Decimal, Decimal, int]]:
    """Back-adjust (date, open, high, low, close, volume) bars, oldest first:
    every bar dated before an action's ex-date is multiplied by its factor
    (volume divided), compounding across several actions. Bars on or after
    the ex-date are already on the new basis and are untouched."""
    out = []
    for d, o, h, l, c, v in bars:
        f = Fraction(1)
        for ex, factor in actions:
            if d < ex:
                f *= factor
        if f == 1:
            out.append((d, o, h, l, c, v))
            continue
        fd = Decimal(f.numerator) / Decimal(f.denominator)
        q = Decimal("0.01")
        out.append((d, (o * fd).quantize(q), (h * fd).quantize(q), (l * fd).quantize(q), (c * fd).quantize(q),
                    int(v / fd) if fd else v))
    return out
