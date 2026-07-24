from datetime import date

from tabfm.trading.pipeline.paper_executor import (
  _apply_friction, execute_paper_trade, format_recommendation,
)
from tabfm.trading.store.journal import init_db, get_open_trades

_TRADE = {
  "ticker": "SPY", "direction": "put_spread", "strike_short": 700.0,
  "strike_long": 695.0, "expiry": "2026-08-21", "dte": 28,
  "entry_credit": 2.25, "spread_width_dollars": 5.0, "bid_ask_pct": 0.10,
  "contracts": 2, "pop_predicted": 0.7, "exp_return": 0.2,
  "vix_bucket": "normal", "trend_direction": "sideways", "iv_regime": "fair",
  "total_risk": 550.0, "iv_rank": 50.0,
}


def test_friction_formula(monkeypatch):
  # Default TABFM_SLIPPAGE_FRAC=0.0, TABFM_FEES_RT=0.20
  # 2.25 - 0.0 * (0.10 * 2.25) - 0.20/100 = 2.25 - 0.002 = 2.248 -> 2.25
  assert _apply_friction(2.25, 0.10) == 2.25
  # With fees big enough to see the difference
  monkeypatch.setenv("TABFM_FEES_RT", "2.0")
  # 2.25 - 0.0 * 0.225 - 2.0/100 = 2.25 - 0.02 = 2.23
  assert _apply_friction(2.25, 0.10) == 2.23


def test_friction_floor(monkeypatch):
  monkeypatch.setenv("TABFM_FEES_RT", "10.0")
  assert _apply_friction(0.05, 0.0) == 0.01


def test_friction_missing_spread_fees_only(monkeypatch):
  # Default fees: 2.25 - 0 - 0.002 -> 2.25 (rounds back)
  assert _apply_friction(2.25, 0.0) == 2.25
  # Visible with bigger fees
  monkeypatch.setenv("TABFM_FEES_RT", "2.0")
  assert _apply_friction(2.25, 0.0) == 2.23


def test_friction_env_overrides(monkeypatch):
  monkeypatch.setenv("TABFM_SLIPPAGE_FRAC", "1.0")
  monkeypatch.setenv("TABFM_FEES_RT", "0.0")
  # 2.25 - 1.0 * (0.10 * 2.25) - 0.0 = 2.25 - 0.225 = 2.025 -> 2.02 or 2.03 (float repr)
  assert _apply_friction(2.25, 0.10) in (2.02, 2.03)


def test_executor_stores_adjusted_and_mid(tmp_path):
  db = tmp_path / "j.db"
  init_db(db)
  execute_paper_trade(dict(_TRADE), date(2026, 7, 24), db)
  row = get_open_trades(db)[0]
  # Default fees: 2.25 - 0.002 = 2.248 -> 2.25; mid = 2.25 * 1.05 = 2.3625 -> 2.36
  assert row["entry_credit"] == 2.25
  assert row["entry_credit_mid"] == round(2.25 * 1.05, 2)  # == 2.36
  # max_loss / max_profit derive from the fill credit
  assert row["max_loss"] == round(2 * (5.0 - 2.25) * 100, 2)
  assert row["max_profit"] == round(2 * 2.25 * 100, 2)


def test_recommendation_shows_fill_and_mid():
  out = format_recommendation(dict(_TRADE), 1, date(2026, 7, 24))
  # fill = 2.25, mid = 2.36
  assert "2.25" in out and "2.36" in out
