from tabfm.trading.pipeline.bankroll import Bankroll
from tabfm.trading.pipeline.trade_recommender import select_trade, _passes_filters

_GOOD = {
  "ticker": "SPY", "direction": "put_spread",
  "spread_width_dollars": 5.0, "entry_credit": 2.25,
  "strike_short": 480.0, "strike_long": 475.0, "expiry": "2026-08-21",
  "bid_ask_pct": 0.10, "open_interest": 200, "dte": 14, "short_delta": 0.25,
  "earnings_flag": "no_earnings", "pop_predicted": 0.72, "exp_return": 0.20,
}

_OPEN_SAME = {
  "ticker": "SPY", "direction": "put_spread",
  "strike_short": 480.0, "strike_long": 475.0, "expiry": "2026-08-21",
  "max_loss": 275.0,
}


def _bk(equity=2000.0, slice_frac=0.15, exposure_frac=0.45, recovery=False):
  frac = slice_frac * (0.5 if recovery else 1.0)
  return Bankroll(
    starting=2000.0, realized=equity - 2000.0, equity=equity,
    peak_equity=max(equity, 2000.0), drawdown_pct=0.0, recovery_mode=recovery,
    slice_limit=round(equity * frac, 2),
    exposure_limit=round(equity * exposure_frac, 2),
  )


# ---- filter gauntlet (unchanged checks) ----

def test_passes_filters_good_candidate():
  assert _passes_filters(_GOOD)


def test_filter_rejects_wide_bid_ask():
  assert not _passes_filters({**_GOOD, "bid_ask_pct": 0.20})


def test_filter_rejects_low_oi():
  assert not _passes_filters({**_GOOD, "open_interest": 50})


def test_filter_rejects_dte_out_of_range():
  assert not _passes_filters({**_GOOD, "dte": 3})
  assert not _passes_filters({**_GOOD, "dte": 60})


def test_filter_rejects_delta_out_of_range():
  assert not _passes_filters({**_GOOD, "short_delta": 0.05})
  assert not _passes_filters({**_GOOD, "short_delta": 0.50})


def test_filter_rejects_earnings_week():
  assert not _passes_filters({**_GOOD, "earnings_flag": "earnings_week"})


def test_wide_spread_no_longer_filtered_but_must_fit_slice():
  # Old static $1000 width check is gone: a 15-wide spread passes the gauntlet
  wide = {**_GOOD, "spread_width_dollars": 15.0, "entry_credit": 5.0}
  assert _passes_filters(wide)
  # ...but (15 - 5) * 100 = $1000/contract doesn't fit a $300 slice → skipped
  assert select_trade([wide], bankroll=_bk()) is None


# ---- selection ----

def test_select_trade_returns_best():
  candidates = [
    {**_GOOD, "pop_predicted": 0.60, "exp_return": 0.10, "ticker": "SPY"},
    {**_GOOD, "pop_predicted": 0.72, "exp_return": 0.20, "ticker": "TSLA"},
  ]
  best = select_trade(candidates, bankroll=_bk())
  assert best["ticker"] == "TSLA"


def test_select_trade_returns_none_when_all_fail_filters():
  assert select_trade([{**_GOOD, "dte": 2}], bankroll=_bk()) is None


def test_select_trade_skips_negative_ev():
  candidates = [{**_GOOD, "pop_predicted": 0.72, "exp_return": -0.10}]
  assert select_trade(candidates, bankroll=_bk()) is None


# ---- dedup ----

def test_dedup_skips_identical_open_position():
  assert select_trade([dict(_GOOD)], open_trades=[_OPEN_SAME], bankroll=_bk()) is None


def test_dedup_allows_different_strikes():
  different = {**_GOOD, "strike_short": 470.0, "strike_long": 465.0}
  best = select_trade([different], open_trades=[_OPEN_SAME], bankroll=_bk())
  assert best is not None
  assert best["strike_short"] == 470.0


# ---- bankroll sizing ----

def test_slice_sizes_one_contract_at_default_equity():
  # slice $300, (5 - 2.25) * 100 = $275/contract → exactly 1 contract
  best = select_trade([dict(_GOOD)], bankroll=_bk())
  assert best["contracts"] == 1
  assert best["total_risk"] == 275.0  # credit-adjusted: 1 * (5 - 2.25) * 100


def test_larger_equity_sizes_more_contracts():
  # equity 6000 → slice 900 → floor(900 / 275) = 3 contracts
  best = select_trade([dict(_GOOD)], bankroll=_bk(equity=6000.0))
  assert best["contracts"] == 3


def test_exposure_budget_blocks_when_book_full():
  # exposure 900; open risk 800 → budget min(300, 100) < 275 → no trade
  opens = [
    {**_OPEN_SAME, "strike_short": 470.0, "max_loss": 400.0},
    {**_OPEN_SAME, "strike_short": 460.0, "max_loss": 400.0},
  ]
  assert select_trade(
    [{**_GOOD, "strike_short": 450.0, "strike_long": 445.0}],
    open_trades=opens, bankroll=_bk(),
  ) is None


def test_recovery_mode_halves_slice():
  # recovery: slice 2000 * 0.075 = 150 < 275 → no trade even with empty book
  assert select_trade([dict(_GOOD)], bankroll=_bk(recovery=True)) is None


def test_max_contracts_cap_binds():
  # Huge equity, tiny per-contract loss: slice 15000, loss $75 → 200 raw, capped 10
  cheap = {**_GOOD, "spread_width_dollars": 1.0, "entry_credit": 0.25}
  best = select_trade([cheap], bankroll=_bk(equity=100_000.0))
  assert best["contracts"] == 10


def test_zero_budget_returns_none_without_scoring():
  bk = _bk(equity=0.0)
  bk = Bankroll(**{**bk.__dict__, "slice_limit": 0.0, "exposure_limit": 0.0})
  assert select_trade([dict(_GOOD)], bankroll=bk) is None


def test_legacy_none_bankroll_uses_defaults(monkeypatch):
  monkeypatch.delenv("TABFM_STARTING_CAPITAL", raising=False)
  best = select_trade([dict(_GOOD)])  # no bankroll arg
  assert best is not None
  assert best["contracts"] == 1  # default $2k equity → $300 slice → 1 contract
