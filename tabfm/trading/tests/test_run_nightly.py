"""Integration test: runs the full nightly pipeline with HistAdapter on a past date."""
from datetime import date
from unittest.mock import MagicMock, patch
import numpy as np
from tabfm.trading.store.journal import init_db, get_open_trades

AS_OF = date(2025, 6, 1)


def _mock_models():
  clf = MagicMock()
  reg = MagicMock()
  return clf, reg


def test_run_produces_trade_or_none(tmp_path):
  from tabfm.trading.adapters.historical import HistAdapter
  from tabfm.trading.run_nightly import run

  db = tmp_path / "test.db"
  store = tmp_path / "store.parquet"
  init_db(db)

  clf, reg = _mock_models()
  with patch("tabfm.trading.pipeline.tabfm_scorer.TabFMClassifier") as MockClf, \
       patch("tabfm.trading.pipeline.tabfm_scorer.TabFMRegressor") as MockReg:
    MockClf.return_value.predict_proba.return_value = np.array([[0.28, 0.72]])
    MockReg.return_value.predict.return_value = np.array([0.20])
    adapter = HistAdapter(as_of=AS_OF)
    result = run(adapter, clf, reg, as_of=AS_OF, db_path=db, store_path=store)

  # result is either the best trade dict or None (if all candidates filtered)
  assert result is None or isinstance(result, dict)


def test_run_logs_to_journal_when_trade_found(tmp_path):
  from tabfm.trading.adapters.historical import HistAdapter
  from tabfm.trading.run_nightly import run

  db = tmp_path / "test.db"
  store = tmp_path / "store.parquet"
  init_db(db)

  with patch("tabfm.trading.pipeline.tabfm_scorer.TabFMClassifier") as MockClf, \
       patch("tabfm.trading.pipeline.tabfm_scorer.TabFMRegressor") as MockReg:
    MockClf.return_value.predict_proba.return_value = np.array([[0.28, 0.72]])
    MockReg.return_value.predict.return_value = np.array([0.20])
    adapter = HistAdapter(as_of=AS_OF)
    _, reg = _mock_models()
    clf, _ = _mock_models()
    result = run(adapter, clf, reg, as_of=AS_OF, db_path=db, store_path=store)

  if result is not None:
    assert len(get_open_trades(db)) == 1
