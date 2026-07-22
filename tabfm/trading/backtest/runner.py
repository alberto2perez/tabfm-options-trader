from datetime import date, timedelta
from pathlib import Path

from ..adapters.historical import HistAdapter
from ..pipeline.accuracy_tracker import report
from ..run_nightly import run
from ..store.journal import _DEFAULT_DB, init_db
from ..store.history_store import _DEFAULT_STORE


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
  db_path: Path = _DEFAULT_DB,
  store_path: Path = _DEFAULT_STORE,
) -> dict:
  """Walk-forward backtest over lookback_days calendar days ending at as_of."""
  if as_of is None:
    as_of = date.today()

  start = as_of - timedelta(days=lookback_days)
  days = trading_days(start, as_of - timedelta(days=1))

  init_db(db_path)
  from tabfm import tabfm_v1_0_0_pytorch as tabfm_backend
  clf_model = tabfm_backend.load(model_type="classification")
  reg_model = tabfm_backend.load(model_type="regression")

  print(f"[Backtest] {len(days)} trading days from {start} to {as_of}")

  for i, sim_date in enumerate(days):
    adapter = HistAdapter(as_of=sim_date)
    run(adapter, clf_model, reg_model, as_of=sim_date,
        db_path=db_path, store_path=store_path)
    if (i + 1) % 20 == 0:
      print(f"[Backtest] {i+1}/{len(days)} days complete")

  return report(db_path=db_path, verbose=True)
