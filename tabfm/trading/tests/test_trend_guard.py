from datetime import date

import pandas as pd

from tabfm.trading.pipeline.trend_guard import assess_trend_risk

AS_OF = date(2026, 7, 24)


class _Stub:
  """close/sma set the trend; short_mid/long_mid set the spread mark."""
  def __init__(self, close, sma20, sma50, short_mid, long_mid):
    self.close, self.sma20, self.sma50 = close, sma20, sma50
    self.short_mid, self.long_mid = short_mid, long_mid

  def get_underlying(self, ticker, as_of):
    return {"close": self.close, "sma20": self.sma20, "sma50": self.sma50}

  def get_options_chain(self, ticker, as_of):
    return pd.DataFrame([
      {"strike": 680.0, "expiry": pd.Timestamp("2026-08-21"), "option_type": "put",
       "mid": self.short_mid, "bid": self.short_mid, "ask": self.short_mid,
       "open_interest": 500, "delta": 0.3, "iv": 0.2, "dte": 28},
      {"strike": 675.0, "expiry": pd.Timestamp("2026-08-21"), "option_type": "put",
       "mid": self.long_mid, "bid": self.long_mid, "ask": self.long_mid,
       "open_interest": 500, "delta": 0.2, "iv": 0.2, "dte": 28},
    ])

  def get_vix(self, as_of):
    return 18.0


def _put_trade():
  return dict(trade_id=1, ticker="SPY", direction="put_spread",
              strike_short=680.0, strike_long=675.0, expiry="2026-08-21",
              entry_credit=2.0, spread_width=5.0, contracts=1, max_loss=300.0)


def _downtrend(short_mid, long_mid):
  return _Stub(690.0, 700.0, 710.0, short_mid, long_mid)   # close<sma20<sma50


def test_put_spread_challenged_by_downtrend_losing_consider():
  # mark = 3.0 - 0.2 = 2.8 → unrealized (2-2.8)*100 = -80 → 80/300 = 0.27 → CONSIDER
  alerts = assess_trend_risk([_put_trade()], _downtrend(3.0, 0.2), AS_OF)
  assert len(alerts) == 1
  a = alerts[0]
  assert a["action"] == "CONSIDER CLOSING"
  assert a["trend"] == "downtrend"
  assert a["unrealized"] < 0
  assert "SPY" in a["message"]


def test_put_spread_close_now_at_half_max_loss():
  # mark = 3.7 - 0.2 = 3.5 → unrealized -150 → 150/300 = 0.5 → CLOSE NOW
  alerts = assess_trend_risk([_put_trade()], _downtrend(3.7, 0.2), AS_OF)
  assert alerts[0]["action"] == "CLOSE NOW"
  assert alerts[0]["loss_fraction"] >= 0.5


def test_no_alert_when_trend_favorable():
  # uptrend (close>sma20>sma50) under a put spread → not adverse
  up = _Stub(710.0, 700.0, 690.0, 3.5, 0.2)
  assert assess_trend_risk([_put_trade()], up, AS_OF) == []


def test_no_alert_when_adverse_but_winning():
  # downtrend but mark = 1.0 - 0.2 = 0.8 < credit 2.0 → unrealized +120 → not challenged
  assert assess_trend_risk([_put_trade()], _downtrend(1.0, 0.2), AS_OF) == []


def test_call_spread_challenged_by_uptrend():
  up = _Stub(710.0, 700.0, 690.0, 3.7, 0.2)   # uptrend
  call = dict(trade_id=2, ticker="SPY", direction="call_spread",
              strike_short=680.0, strike_long=685.0, expiry="2026-08-21",
              entry_credit=2.0, spread_width=5.0, contracts=1, max_loss=300.0)
  # NOTE: _spread_mark reads the 'call' side of the chain; extend the stub to
  # serve calls if needed. If the stub only has puts, this test asserts the
  # adverse+trend path via a put-shaped chain is acceptable — keep the stub
  # returning the same two rows but option_type 'call' for this case.
  alerts = assess_trend_risk([call], _CallStub(710.0, 700.0, 690.0, 3.7, 0.2), AS_OF)
  assert alerts and alerts[0]["trend"] == "uptrend"


def test_disabled_via_env(monkeypatch):
  monkeypatch.setenv("TABFM_TREND_GUARD", "off")
  assert assess_trend_risk([_put_trade()], _downtrend(3.7, 0.2), AS_OF) == []


class _CallStub(_Stub):
  def get_options_chain(self, ticker, as_of):
    return pd.DataFrame([
      {"strike": 680.0, "expiry": pd.Timestamp("2026-08-21"), "option_type": "call",
       "mid": self.short_mid, "bid": self.short_mid, "ask": self.short_mid,
       "open_interest": 500, "delta": 0.3, "iv": 0.2, "dte": 28},
      {"strike": 685.0, "expiry": pd.Timestamp("2026-08-21"), "option_type": "call",
       "mid": self.long_mid, "bid": self.long_mid, "ask": self.long_mid,
       "open_interest": 500, "delta": 0.2, "iv": 0.2, "dte": 28},
    ])
