"""Replay the strategy over real historical option marks (cached parquet).

Trustworthy closed-trade statistics on REAL prices — the evidence a 2-week
live paper window cannot produce. Isolated temp journal/store; never touches
the live data/ book."""
import tempfile
from datetime import date
from pathlib import Path

import pandas as pd

from ..adapters.replay import ReplayAdapter
from ..pipeline.accuracy_tracker import report
from ..pipeline.turning_points import turning_point_report
from ..run_nightly import run
from ..store.journal import init_db
from .runner import trading_days


def run_replay(cache_path, clf_model=None, reg_model=None, tickers=("SPY",)) -> dict:
  chains = pd.read_parquet(cache_path)
  dates = sorted(date.fromisoformat(str(d)) for d in chains["date"].unique())
  days = trading_days(dates[0], dates[-1])

  scratch = Path(tempfile.mkdtemp(prefix="tabfm_replay_"))
  db_path = scratch / "journal.db"
  store_path = scratch / "store.parquet"
  print(f"[Replay] isolated data dir: {scratch}")
  init_db(db_path)

  if clf_model is None or reg_model is None:
    import torch
    from tabfm import tabfm_v1_0_0_pytorch as tabfm_backend
    device = "mps" if torch.backends.mps.is_available() else "cpu"
    clf_model = clf_model or tabfm_backend.load(model_type="classification", device=device)
    reg_model = reg_model or tabfm_backend.load(model_type="regression", device=device)
    print(f"[Replay] TabFM models on {device}")

  adapter = ReplayAdapter(cache_path, as_of=days[-1])

  # Restrict the watchlist to the replay tickers
  import tabfm.trading.watchlist as wl
  import tabfm.trading.pipeline.chain_fetcher as cf
  from tabfm.trading.watchlist import Ticker
  live = [Ticker(t, "index_etf") for t in tickers]
  wl.WATCHLIST = live
  cf.WATCHLIST = live

  for i, sim_date in enumerate(days):
    run(adapter, clf_model, reg_model, as_of=sim_date, db_path=db_path, store_path=store_path)
    if (i + 1) % 20 == 0:
      print(f"[Replay] {i+1}/{len(days)} days")

  metrics = report(db_path=db_path, verbose=True)
  turning_point_report(store_path, db_path, verbose=True)
  return metrics
