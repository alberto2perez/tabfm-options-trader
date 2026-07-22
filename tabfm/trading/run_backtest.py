"""Walk-forward backtest entry point.

Usage:
  python -m tabfm.trading.run_backtest
  python -m tabfm.trading.run_backtest --days 90
"""
import argparse
from datetime import date
from .backtest.runner import run_backtest

if __name__ == "__main__":
  parser = argparse.ArgumentParser(description="Run options signal pipeline backtest")
  parser.add_argument("--days", type=int, default=252,
                      help="Calendar days to backtest (default: 252 ≈ 1 year)")
  args = parser.parse_args()
  run_backtest(lookback_days=args.days)
