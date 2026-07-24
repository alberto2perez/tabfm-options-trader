from datetime import date, timedelta
from pathlib import Path

from ..adapters.historical import HistAdapter
from ..pipeline.accuracy_tracker import report
from ..pipeline.turning_points import turning_point_report
from ..run_nightly import run
from ..store.journal import init_db


def trading_days(start: date, end: date) -> list[date]:
  days, current = [], start
  while current <= end:
    if current.weekday() < 5:
      days.append(current)
    current += timedelta(days=1)
  return days


def run_backtest(
  lookback_days: int = 252,
  as_of: date | None = None,
  db_path: Path | None = None,
  store_path: Path | None = None,
) -> dict:
  """Walk-forward backtest over lookback_days calendar days ending at as_of.

  With no explicit paths, journal/store live in a fresh temp directory so a
  backtest can never contaminate the live data/ journal, equity walk, or
  calibrator.
  """
  if db_path is None or store_path is None:
    import tempfile
    scratch = Path(tempfile.mkdtemp(prefix="tabfm_backtest_"))
    db_path = db_path or scratch / "journal.db"
    store_path = store_path or scratch / "store.parquet"
    print(f"[Backtest] isolated data dir: {db_path.parent}")

  if as_of is None:
    as_of = date.today()

  start = as_of - timedelta(days=lookback_days)
  days = trading_days(start, as_of - timedelta(days=1))

  init_db(db_path)
  import torch
  from tabfm import tabfm_v1_0_0_pytorch as tabfm_backend
  device = "mps" if torch.backends.mps.is_available() else "cpu"
  clf_model = tabfm_backend.load(model_type="classification", device=device)
  reg_model = tabfm_backend.load(model_type="regression", device=device)
  print(f"[Backtest] TabFM models on {device}")

  print(f"[Backtest] {len(days)} trading days from {start} to {as_of}")

  # One adapter for the whole run — yfinance history is downloaded once per
  # ticker and cached. get_underlying() filters to each sim_date internally.
  adapter = HistAdapter(as_of=days[-1])

  for i, sim_date in enumerate(days):
    run(adapter, clf_model, reg_model, as_of=sim_date,
        db_path=db_path, store_path=store_path)
    if (i + 1) % 20 == 0:
      print(f"[Backtest] {i+1}/{len(days)} days complete")

  metrics = report(db_path=db_path, verbose=True)
  turning_point_report(store_path, db_path, verbose=True)
  return metrics
