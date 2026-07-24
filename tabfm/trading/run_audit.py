"""Midday audit-only entry point.

  python -m tabfm.trading.run_audit                 # live Robinhood adapter
  python -m tabfm.trading.run_audit --snapshot PATH # from a midday snapshot
"""
import sys
from datetime import date

from .run_nightly import run_audit_only


def main(argv: list[str]) -> None:
  if "--snapshot" in argv:
    path = argv[argv.index("--snapshot") + 1]
    from .adapters.snapshot import SnapshotAdapter
    adapter = SnapshotAdapter(path)
  else:
    import os
    import robin_stocks.robinhood as rh
    try:
      from dotenv import load_dotenv
      load_dotenv()
    except ImportError:
      pass
    user, pw = os.environ.get("RH_USER"), os.environ.get("RH_PASS")
    if user and pw:
      rh.login(user, pw)
    else:
      rh.login()
    from .adapters.live import LiveAdapter
    adapter = LiveAdapter()
  run_audit_only(adapter, date.today())


if __name__ == "__main__":
  main(sys.argv[1:])
