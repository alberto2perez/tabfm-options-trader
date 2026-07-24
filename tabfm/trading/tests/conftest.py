import pytest


@pytest.fixture(autouse=True)
def _clean_bankroll_env(monkeypatch):
  """Sizing tests assume default bankroll config; shell exports must not leak in."""
  for var in (
    "TABFM_STARTING_CAPITAL", "TABFM_RISK_PER_TRADE",
    "TABFM_MAX_EXPOSURE", "TABFM_DRAWDOWN_BRAKE",
  ):
    monkeypatch.delenv(var, raising=False)
