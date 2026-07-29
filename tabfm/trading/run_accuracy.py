"""Live accuracy / performance report entry point.

Prints the accuracy_tracker scorecard (win rate, POP calibration error,
Brier TabFM-vs-market, cumulative P&L, max drawdown, best/worst regime,
model-vs-dumb-baseline) computed over the CLOSED trades in the live journal.

  python -m tabfm.trading.run_accuracy                 # live data/journal.db
  python -m tabfm.trading.run_accuracy --db PATH       # a specific journal

Until live trades close (spreads are 28-45 DTE), the live journal has no
closed trades and this prints "No closed trades yet." For trustworthy
numbers before then, run the real-marks replay:
  python -m tabfm.trading.run_backtest
"""
import argparse

from .pipeline.accuracy_tracker import report
from .store.journal import _DEFAULT_DB


def main(argv: list[str] | None = None) -> dict:
  parser = argparse.ArgumentParser(description="Live accuracy / performance report")
  parser.add_argument("--db", default=str(_DEFAULT_DB),
                      help="Path to the journal DB (default: live data/journal.db)")
  args = parser.parse_args(argv)
  return report(db_path=args.db, verbose=True)


if __name__ == "__main__":
  main()
