from datetime import date
import pandas as pd


def _vix_bucket(vix: float) -> str:
  if vix < 15:
    return "low"
  if vix < 25:
    return "normal"
  if vix < 35:
    return "elevated"
  return "spike"


def _trend_direction(close: float, sma20: float, sma50: float) -> str:
  if close > sma20 and sma20 > sma50:
    return "uptrend"
  if close < sma20 and sma20 < sma50:
    return "downtrend"
  return "sideways"


def _iv_regime(iv_rank: float) -> str:
  if iv_rank < 25:
    return "cheap"
  if iv_rank < 75:
    return "fair"
  return "expensive"


def engineer_features(
  chain_data: dict, as_of: date, iv_rank: float, extra_features: dict | None = None
) -> list[dict]:
  """Generate one feature row per candidate vertical spread.

  Args:
    chain_data: dict from ChainFetcher with ticker/sector/underlying/chain/vix
    as_of: signal date
    iv_rank: IV percentile for this ticker (0-100)
    extra_features: optional dict of event-context features to merge into rows

  Returns: list of feature dicts, one per viable spread candidate
  """
  extra = {
    "days_to_next_megacap_earnings": 99.0,
    "days_to_next_macro_event": 99.0,
    "vix_5d_change": 0.0,
    "iv_spike_score": 0.0,
    **(extra_features or {}),
  }
  ticker = chain_data["ticker"]
  sector = chain_data["sector"]
  u = chain_data["underlying"]
  chain = chain_data["chain"]
  vix = chain_data["vix"]

  S = u["close"]
  vix_bkt = _vix_bucket(vix)
  trend = _trend_direction(S, u["sma20"], u["sma50"])
  iv_reg = _iv_regime(iv_rank)

  rows = []
  for opt_type in ("call", "put"):
    direction = "call_spread" if opt_type == "call" else "put_spread"
    subset = chain[chain["option_type"] == opt_type].copy()
    short_legs = subset[(subset["delta"] >= 0.15) & (subset["delta"] <= 0.40)]

    for _, short in short_legs.iterrows():
      dte = int(short["dte"])
      if not (7 <= dte <= 45):
        continue
      exp = short["expiry"]
      same_expiry = subset[subset["expiry"] == exp]

      if opt_type == "put":
        long_cands = same_expiry[same_expiry["strike"] < short["strike"]]
        long = long_cands.iloc[-1] if not long_cands.empty else None
      else:
        long_cands = same_expiry[same_expiry["strike"] > short["strike"]]
        long = long_cands.iloc[0] if not long_cands.empty else None

      if long is None:
        continue

      spread_width = abs(float(short["strike"]) - float(long["strike"]))
      entry_credit = float(short["bid"]) - float(long["ask"])
      if entry_credit <= 0 or spread_width <= 0:
        continue

      max_loss = spread_width - entry_credit
      if max_loss <= 0:
        continue

      ba_spread = (short["ask"] - short["bid"]) + (long["ask"] - long["bid"])
      bid_ask_pct = float(ba_spread) / entry_credit if entry_credit > 0 else 999.0
      expiry_date = exp.date() if hasattr(exp, "date") else exp
      expiry_type = "weekly" if dte <= 14 else "monthly"

      rows.append({
        "date": str(as_of),
        "ticker": ticker,
        "sector": sector,
        "direction": direction,
        "strike_short": float(short["strike"]),
        "strike_long": float(long["strike"]),
        "expiry": str(expiry_date),
        "dte": dte,
        "expiry_type": expiry_type,
        "entry_credit": round(entry_credit, 2),
        "spread_width_dollars": round(spread_width, 2),
        "max_profit": round(entry_credit, 2),
        "max_loss": round(max_loss, 2),
        "short_delta": round(float(short["delta"]), 4),
        "strike_distance_pct": round(abs(S - float(short["strike"])) / S * 100, 2),
        "bid_ask_pct": round(bid_ask_pct, 4),
        "open_interest": int(short.get("open_interest", 0)),
        "price_close": round(S, 2),
        "momentum_5d": round(u["momentum_5d"] * 100, 4),
        "momentum_20d": round(u["momentum_20d"] * 100, 4),
        "atr_14": round(u["atr14"], 4),
        "volume_zscore": round(u["volume_zscore"], 4),
        "price_vs_sma20": round((S - u["sma20"]) / u["sma20"] * 100, 4),
        "vix_level": round(vix, 2),
        "vix_5d_change": extra["vix_5d_change"],
        "days_to_next_megacap_earnings": extra["days_to_next_megacap_earnings"],
        "days_to_next_macro_event": extra["days_to_next_macro_event"],
        "iv_spike_score": extra["iv_spike_score"],
        "rsi_14": round(u["rsi_14"], 2),
        "macd_line": round(u["macd_line"], 4),
        "macd_signal": round(u["macd_signal"], 4),
        "macd_histogram": round(u["macd_histogram"], 4),
        "iv_rank": round(iv_rank, 2),
        "hv20": round(u["hv20"], 4),
        "hv_iv_ratio": round(u["hv20"] / float(short["iv"]), 4) if float(short["iv"]) > 0 else 0.0,
        "vix_bucket": vix_bkt,
        "trend_direction": trend,
        "iv_regime": iv_reg,
        "earnings_flag": "no_earnings",
      })
  return rows
