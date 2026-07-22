from datetime import date
from tabfm.trading.backtest.runner import trading_days


def test_trading_days_excludes_weekends():
  # 2025-01-06 is Monday, 2025-01-10 is Friday
  days = trading_days(date(2025, 1, 6), date(2025, 1, 10))
  assert len(days) == 5
  for d in days:
    assert d.weekday() < 5


def test_trading_days_single_day():
  days = trading_days(date(2025, 1, 6), date(2025, 1, 6))
  assert days == [date(2025, 1, 6)]


def test_trading_days_across_weekend():
  # Friday to Monday = 2 trading days
  days = trading_days(date(2025, 1, 10), date(2025, 1, 13))
  assert len(days) == 2
  assert days[0] == date(2025, 1, 10)
  assert days[1] == date(2025, 1, 13)
