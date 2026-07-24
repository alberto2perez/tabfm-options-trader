from tabfm.trading.pipeline.trade_recommender import select_trade, _passes_filters, _contracts

_GOOD = {
  "ticker": "SPY", "direction": "put_spread",
  "spread_width_dollars": 5.0, "contracts": 2, "total_risk": 1000.0,
  "bid_ask_pct": 0.10, "open_interest": 200, "dte": 14, "short_delta": 0.25,
  "earnings_flag": "no_earnings", "pop_predicted": 0.72, "exp_return": 0.20,
}


def test_passes_filters_good_candidate():
  assert _passes_filters(_GOOD)


def test_filter_rejects_over_1k():
  bad = {**_GOOD, "spread_width_dollars": 15.0}  # 15 * 100 = $1500 for 1 contract
  assert not _passes_filters(bad)


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


def test_contracts_calculation():
  assert _contracts(5.0) == 2   # floor(1000 / 500) = 2
  assert _contracts(2.5) == 4   # floor(1000 / 250) = 4
  assert _contracts(0.5) == 10  # floor(1000 / 50) = 20, capped at 10


def test_select_trade_returns_best():
  candidates = [
    {**_GOOD, "pop_predicted": 0.60, "exp_return": 0.10, "ticker": "SPY"},
    {**_GOOD, "pop_predicted": 0.72, "exp_return": 0.20, "ticker": "TSLA"},
  ]
  best = select_trade(candidates)
  assert best["ticker"] == "TSLA"  # score = 0.72 * 0.20 = 0.144 > 0.60 * 0.10


def test_select_trade_returns_none_when_all_fail_filters():
  bad = [{**_GOOD, "dte": 2}]
  assert select_trade(bad) is None


def test_select_trade_skips_negative_ev():
  candidates = [{**_GOOD, "pop_predicted": 0.72, "exp_return": -0.10}]
  assert select_trade(candidates) is None


_FULL = {
  **_GOOD,
  "strike_short": 480.0, "strike_long": 475.0, "expiry": "2026-08-21",
  "entry_credit": 2.25,
}

_OPEN_SAME = {
  "ticker": "SPY", "direction": "put_spread",
  "strike_short": 480.0, "strike_long": 475.0, "expiry": "2026-08-21",
  "max_loss": 550.0,
}


def test_dedup_skips_identical_open_position():
  assert select_trade([dict(_FULL)], open_trades=[_OPEN_SAME]) is None


def test_dedup_allows_different_strikes():
  different = {**_FULL, "strike_short": 470.0, "strike_long": 465.0}
  best = select_trade([different], open_trades=[_OPEN_SAME])
  assert best is not None
  assert best["strike_short"] == 470.0


def test_portfolio_cap_blocks_when_budget_exhausted():
  # Two open positions totaling $1,450 of max loss; candidate needs $275/contract
  opens = [{**_OPEN_SAME, "max_loss": 900.0},
           {**_OPEN_SAME, "strike_short": 470.0, "max_loss": 550.0}]
  assert select_trade([{**_FULL, "strike_short": 460.0, "strike_long": 455.0}],
                      open_trades=opens, max_portfolio_risk=1500.0) is None


def test_portfolio_cap_sizes_contracts_down_to_fit():
  # $1,100 open risk, $400 budget: (5 - 2.25) * 100 = $275/contract -> 1 contract
  opens = [{**_OPEN_SAME, "max_loss": 550.0},
           {**_OPEN_SAME, "strike_short": 470.0, "max_loss": 550.0}]
  best = select_trade([{**_FULL, "strike_short": 460.0, "strike_long": 455.0}],
                      open_trades=opens, max_portfolio_risk=1500.0)
  assert best is not None
  assert best["contracts"] == 1


def test_portfolio_cap_full_size_when_no_open_positions():
  best = select_trade([dict(_FULL)], open_trades=[], max_portfolio_risk=1500.0)
  assert best is not None
  assert best["contracts"] == 2  # per-trade sizing unchanged
