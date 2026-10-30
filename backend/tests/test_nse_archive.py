"""NSE archive parsing and corporate-action adjustment (services/nse_archive.py).

The survivorship research rests on these: one wrong bonus factor turns a
delisted company's history into a fake crash or hides a real one.
"""

import io
import zipfile
from datetime import date
from decimal import Decimal
from fractions import Fraction

from app.services.nse_archive import (
    adjust_series, adjustment_factor, bhavcopy_url, parse_bhavcopy, parse_ex_date,
)


def _zip(name: str, text: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(name, text)
    return buf.getvalue()


def test_the_url_follows_the_format_nse_used_on_that_date():
    assert bhavcopy_url(date(2018, 4, 2)).endswith("/historical/EQUITIES/2018/APR/cm02APR2018bhav.csv.zip")
    assert bhavcopy_url(date(2024, 7, 5)).endswith("/2024/JUL/cm05JUL2024bhav.csv.zip")
    assert bhavcopy_url(date(2024, 7, 8)).endswith("/cm/BhavCopy_NSE_CM_0_0_0_20240708_F_0000.csv.zip")


def test_the_legacy_format_parses_and_a_row_missing_a_price_is_dropped():
    text = ("SYMBOL,SERIES,OPEN,HIGH,LOW,CLOSE,LAST,PREVCLOSE,TOTTRDQTY,TOTTRDVAL,TIMESTAMP,TOTALTRADES,ISIN,\n"
            "DHFL,EQ,515.1,526.1,512.25,523.05,523,515,2167081,1124344818.05,02-APR-2018,1,INE202B01012,\n"
            "BROKEN,EQ,10,,9,10,10,10,100,1000,02-APR-2018,1,INE000000000,\n")
    rows = parse_bhavcopy(_zip("cm02APR2018bhav.csv", text), date(2018, 4, 2))
    assert [r.symbol for r in rows] == ["DHFL"]
    r = rows[0]
    assert (r.series, r.isin, r.close, r.volume, r.traded_value) == (
        "EQ", "INE202B01012", Decimal("523.05"), 2167081, Decimal("1124344818.05"))


def test_the_udiff_format_parses():
    text = ("TradDt,BizDt,Sgmt,Src,FinInstrmTp,FinInstrmId,ISIN,TckrSymb,SctySrs,XpryDt,FininstrmActlXpryDt,"
            "StrkPric,OptnTp,FinInstrmNm,OpnPric,HghPric,LwPric,ClsPric,LastPric,PrvsClsgPric,UndrlygPric,"
            "SttlmPric,OpnIntrst,ChngInOpnIntrst,TtlTradgVol,TtlTrfVal,TtlNbOfTxsExctd,SsnId,NewBrdLotQty,"
            "Rmks,Rsvd1,Rsvd2,Rsvd3,Rsvd4\n"
            "2025-08-22,2025-08-22,CM,NSE,STK,16921,INE144J01027,20MICRONS,EQ,,,,,20 MICRONS LTD,233.00,241.70,"
            "232.65,234.36,234.60,232.33,,234.45,,,144509,34226143.12,3587,F1,1,,,,,\n")
    rows = parse_bhavcopy(_zip("BhavCopy.csv", text), date(2025, 8, 22))
    assert len(rows) == 1
    assert (rows[0].symbol, rows[0].open, rows[0].close, rows[0].volume) == (
        "20MICRONS", Decimal("233.00"), Decimal("234.36"), 144509)


def test_adjustment_factors_follow_nse_wording():
    assert adjustment_factor(" Bonus 1:1") == Fraction(1, 2)
    assert adjustment_factor("Bonus 2:1") == Fraction(1, 3)
    assert adjustment_factor("Bonus 1:2") == Fraction(2, 3)
    assert adjustment_factor(
        " Face Value Split (Sub-Division) - From Rs 10/- Per Share To Rs 2/- Per Share") == Fraction(1, 5)
    assert adjustment_factor(
        "Face Value Split (Sub-Division) - From Rs 10/- Per Share To Re 1/- Per Share") == Fraction(1, 10)
    assert adjustment_factor("Interim Dividend - Rs 5 Per Share") is None
    assert adjustment_factor("Rights 1:5 @ Premium Rs 100/-") is None


def test_ex_dates_parse_in_nses_formats():
    assert parse_ex_date("15-Mar-2018") == date(2018, 3, 15)
    assert parse_ex_date("not a date") is None


def test_bars_before_each_ex_date_are_adjusted_and_compound():
    bars = [
        (date(2020, 1, 1), Decimal("100"), Decimal("110"), Decimal("90"), Decimal("100"), 1000),
        (date(2020, 6, 1), Decimal("50"), Decimal("55"), Decimal("45"), Decimal("50"), 2000),
        (date(2021, 1, 1), Decimal("10"), Decimal("11"), Decimal("9"), Decimal("10"), 10000),
    ]
    actions = [(date(2020, 3, 1), Fraction(1, 2)),   # bonus 1:1 between bar 1 and 2
               (date(2020, 9, 1), Fraction(1, 5))]   # split 10 -> 2 between bar 2 and 3
    out = adjust_series(bars, actions)
    assert out[0][1:5] == (Decimal("10.00"), Decimal("11.00"), Decimal("9.00"), Decimal("10.00"))
    assert out[0][5] == 10000
    assert out[1][1:5] == (Decimal("10.00"), Decimal("11.00"), Decimal("9.00"), Decimal("10.00"))
    assert out[2] == bars[2], "bars on or after the last ex-date are already on the new basis"
