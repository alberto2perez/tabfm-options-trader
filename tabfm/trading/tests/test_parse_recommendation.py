import json
import pathlib
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parents[3]
SCRIPT = REPO / "scripts" / "parse_recommendation.py"

_CALL_CARD = """# Nightly Recommendations

## 2026-07-24

```
==============================================
  NIGHTLY RECOMMENDATION  ·  2026-07-24
==============================================
  Ticker       SPY
  Direction    CALL CREDIT SPREAD  (bearish/neutral)
  Strikes      $750.0 / $755.0
  Expiry       2026-08-21  (28 DTE)
  Spread Width $5.0
  Entry Credit $2.15 est. fill (mid $2.19)
  Max Profit   $2.15 / contract
  Max Loss     $2.85 / contract
  Contracts    1  ->  max exposure $285
==============================================
  [PAPER LOGGED]  trade_id: 2
```
"""

_PUT_CARD = _CALL_CARD.replace(
    "CALL CREDIT SPREAD  (bearish/neutral)", "PUT CREDIT SPREAD  (bullish/neutral)"
).replace("$750.0 / $755.0", "$620.0 / $615.0")

_GATED_CARD = """# Nightly Recommendations

## 2026-07-28

GATED — no new entries.
- FOMC rate decision next session
"""

_EMPTY = "# Nightly Recommendations\n"

_MALFORMED = _CALL_CARD.replace("  Contracts    1  ->  max exposure $285\n", "")

_TREND_ALERT = """# Nightly Recommendations

## 2026-07-30 — TREND ALERT

- CLOSE NOW: SPY 750/755 call spread — trend flipped bullish
- CONSIDER CLOSING: QQQ put spread — momentum fading
"""


def _run(md: str, tmp_path) -> dict:
    f = tmp_path / "RECOMMENDATIONS.md"
    f.write_text(md)
    r = subprocess.run(
        [sys.executable, str(SCRIPT), "--file", str(f)],
        capture_output=True, text=True, cwd=str(REPO),
    )
    assert r.returncode == 0, f"nonzero exit: {r.stderr}"
    return json.loads(r.stdout)


def test_call_credit_card(tmp_path):
    d = _run(_CALL_CARD, tmp_path)
    assert d["status"] == "trade"
    assert d["ticker"] == "SPY"
    assert d["direction"] == "call_credit"
    assert d["option_type"] == "call"
    assert d["strike_short"] == 750.0 and d["strike_long"] == 755.0
    assert d["sell_strike"] == 750.0 and d["buy_strike"] == 755.0
    assert d["expiry"] == "2026-08-21" and d["dte"] == 28
    assert d["entry_credit"] == 2.15 and d["entry_credit_mid"] == 2.19
    assert d["contracts"] == 1 and d["spread_width"] == 5.0


def test_put_credit_card(tmp_path):
    d = _run(_PUT_CARD, tmp_path)
    assert d["status"] == "trade"
    assert d["direction"] == "put_credit"
    assert d["option_type"] == "put"
    # short (sold) is the HIGHER strike for a put credit spread
    assert d["sell_strike"] == 620.0 and d["buy_strike"] == 615.0


def test_gated_card(tmp_path):
    d = _run(_GATED_CARD, tmp_path)
    assert d["status"] == "gated"
    assert d["date"] == "2026-07-28"
    assert "FOMC" in (d["reason"] or "")


def test_empty_file(tmp_path):
    assert _run(_EMPTY, tmp_path) == {"status": "none"}


def test_malformed_card(tmp_path):
    d = _run(_MALFORMED, tmp_path)
    assert d["status"] == "error"
    assert "contracts" in d["reason"].lower()


def test_trend_alert_card(tmp_path):
    d = _run(_TREND_ALERT, tmp_path)
    assert d["status"] == "trend_alert"
    assert d["date"] == "2026-07-30"
    assert "CLOSE NOW" in (d["reason"] or "")
    assert "CONSIDER CLOSING" in (d["reason"] or "")
