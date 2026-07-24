import sqlite3
from datetime import date
from pathlib import Path

import pandas as pd

from tabfm.trading.adapters.base import DataAdapter
from tabfm.trading.run_nightly import run


class _GatedStubAdapter(DataAdapter):
  """Minimal adapter: one viable put-spread pair, GOOGL reporting today."""

  def get_underlying(self, ticker, as_of):
    return {
      "close": 700.0, "sma20": 700.0, "sma50": 690.0, "atr14": 9.0,
      "hv20": 0.15, "volume": 1e6, "volume_zscore": 0.0,
      "momentum_5d": 0.01, "momentum_20d": 0.02, "rsi_14": 55.0,
      "macd_line": 1.0, "macd_signal": 0.8, "macd_histogram": 0.2,
    }

  def get_options_chain(self, ticker, as_of):
    return pd.DataFrame([
      {"strike": 680.0, "expiry": pd.Timestamp("2026-08-21"), "option_type": "put",
       "bid": 2.20, "ask": 2.30, "mid": 2.25, "open_interest": 500,
       "delta": 0.25, "iv": 0.20, "dte": 28},
      {"strike": 675.0, "expiry": pd.Timestamp("2026-08-21"), "option_type": "put",
       "bid": 1.60, "ask": 1.70, "mid": 1.65, "open_interest": 500,
       "delta": 0.20, "iv": 0.20, "dte": 28},
    ])

  def get_vix(self, as_of):
    return 18.8

  def get_events(self, as_of):
    return {"earnings": [{"symbol": "GOOGL", "date": str(as_of), "when": "amc"}]}

  def get_vix_history(self, as_of, n=6):
    return [["2026-07-17", 18.0], ["2026-07-20", 18.2], ["2026-07-21", 18.1],
            ["2026-07-22", 18.4], ["2026-07-23", 18.7], ["2026-07-24", 18.8]]


def _patch_watchlist(monkeypatch):
  import tabfm.trading.watchlist as wl
  import tabfm.trading.pipeline.chain_fetcher as cf
  from tabfm.trading.watchlist import Ticker
  lone = [Ticker("SPY", "index_etf")]
  monkeypatch.setattr(wl, "WATCHLIST", lone)
  monkeypatch.setattr(cf, "WATCHLIST", lone)


def test_gated_night_places_no_trade_but_persists_rows(tmp_path, monkeypatch, capsys):
  _patch_watchlist(monkeypatch)
  db = tmp_path / "journal.db"
  store = tmp_path / "store.parquet"

  # Models are never touched on a gated night — pass sentinels that would
  # crash if scoring ran.
  result = run(_GatedStubAdapter(), clf_model=object(), reg_model=object(),
               as_of=date(2026, 7, 24), db_path=db, store_path=store)

  assert result is None
  out = capsys.readouterr().out
  assert "[EventGate] NO NEW ENTRIES" in out
  assert "GOOGL" in out
  assert "PORTFOLIO SUMMARY" in out

  # No journal entry
  conn = sqlite3.connect(db)
  assert conn.execute("SELECT COUNT(*) FROM paper_trades").fetchone()[0] == 0

  # Feature rows still appended, with event features populated
  df = pd.read_parquet(store)
  assert len(df) > 0
  assert float(df["days_to_next_megacap_earnings"].iloc[0]) == 0.0

  # Gated day logged
  md = (tmp_path / "RECOMMENDATIONS.md").read_text()
  assert "GATED" in md

  # Market history recorded
  assert (tmp_path / "market_history.csv").exists()


def test_ungated_night_reaches_selection(tmp_path, monkeypatch, capsys):
  _patch_watchlist(monkeypatch)

  class _CalmStub(_GatedStubAdapter):
    def get_events(self, as_of):
      return {"earnings": []}

  db = tmp_path / "journal.db"
  store = tmp_path / "store.parquet"
  result = run(_CalmStub(), clf_model=object(), reg_model=object(),
               as_of=date(2026, 7, 24), db_path=db, store_path=store)
  # Cold start on an empty store: fallback scoring, credit-yield pick.
  out = capsys.readouterr().out
  assert "[EventGate] NO NEW ENTRIES" not in out
