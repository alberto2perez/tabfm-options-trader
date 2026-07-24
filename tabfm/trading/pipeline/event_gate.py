"""Event risk gate: block new entries on high-event-risk days.

Layer A: calendar (mega-cap earnings from the snapshot + committed macro
calendar). Layer B: market-priced risk (VIX 5-session spike velocity, IV
rising above realized vol). Also computes the strategy-C event-context
features recorded on every row.
"""
import json
import os
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

import numpy as np

MEGA_CAPS = ["AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "TSLA", "AVGO"]

_DEFAULT_MACRO_CALENDAR = Path(__file__).parents[3] / "data" / "macro_calendar.json"
_FEATURE_SENTINEL = 99.0
_EVENT_HORIZON_DAYS = 21


@dataclass
class GateResult:
  gated: bool
  reasons: list = field(default_factory=list)
  degraded: bool = False
  features: dict = field(default_factory=dict)


def load_macro_calendar(path: Path | None = None) -> list[dict]:
  path = Path(path) if path is not None else _DEFAULT_MACRO_CALENDAR
  if not path.exists():
    return []
  return json.loads(path.read_text())


def _next_session(d: date) -> date:
  nxt = d + timedelta(days=1)
  while nxt.weekday() >= 5:
    nxt += timedelta(days=1)
  return nxt


def _busdays_until(as_of: date, target: date) -> float:
  if target < as_of:
    return _FEATURE_SENTINEL
  if (target - as_of).days > _EVENT_HORIZON_DAYS:
    return _FEATURE_SENTINEL
  return float(np.busday_count(as_of, target))


def evaluate_event_gate(
  events: dict | None,
  macro_calendar: list,
  vix_history: list,
  chain_stats: dict,
  as_of: date,
) -> GateResult:
  """Evaluate all gate layers for the session.

  events: {"earnings": [{"symbol", "date", "when"}]} from the snapshot, or
    None when the fetch step could not provide earnings data (degraded).
  vix_history: [[YYYY-MM-DD, value], ...] most recent last, incl. today.
  chain_stats: {"median_iv", "hv20", "prev_median_iv"} for the market proxy
    (SPY); may be empty when no chain was fetched.
  """
  reasons: list[str] = []
  degraded = events is None or "earnings" not in (events or {})
  danger = {as_of, _next_session(as_of)}

  # ---- Layer A: mega-cap earnings -------------------------------------
  earnings = (events or {}).get("earnings") or []
  next_earnings = _FEATURE_SENTINEL
  for e in earnings:
    if e.get("symbol") not in MEGA_CAPS:
      continue
    try:
      e_date = date.fromisoformat(str(e["date"]))
    except (KeyError, ValueError):
      continue
    next_earnings = min(next_earnings, _busdays_until(as_of, e_date))
    if e_date in danger:
      when = e.get("when", "unknown")
      day = "today" if e_date == as_of else "next session"
      reasons.append(f"{e['symbol']} reports {day} ({when.upper()})")

  # ---- Layer A: macro calendar ----------------------------------------
  next_macro = _FEATURE_SENTINEL
  for m in macro_calendar or []:
    try:
      m_date = date.fromisoformat(str(m["date"]))
    except (KeyError, ValueError):
      continue
    next_macro = min(next_macro, _busdays_until(as_of, m_date))
    if m_date in danger:
      day = "today" if m_date == as_of else "next session"
      reasons.append(f"{m.get('event', 'macro event')} {day}")

  # ---- Layer B: VIX spike velocity ------------------------------------
  vix_5d_change = 0.0
  if vix_history and len(vix_history) >= 6:
    now = float(vix_history[-1][1])
    base = float(vix_history[-6][1])
    if base > 0:
      vix_5d_change = (now - base) / base
      threshold = float(os.environ.get("TABFM_GATE_VIX_5D", "0.15"))
      if vix_5d_change > threshold:
        reasons.append(f"VIX +{vix_5d_change * 100:.0f}% in 5 sessions")

  # ---- Layer B: IV rising above realized vol --------------------------
  median_iv = (chain_stats or {}).get("median_iv")
  hv20 = (chain_stats or {}).get("hv20")
  prev_iv = (chain_stats or {}).get("prev_median_iv")
  if median_iv and hv20 and prev_iv:
    iv_hv = float(os.environ.get("TABFM_GATE_IV_HV", "1.6"))
    iv_jump = float(os.environ.get("TABFM_GATE_IV_JUMP", "1.2"))
    if median_iv / hv20 > iv_hv and median_iv > prev_iv * iv_jump:
      reasons.append(
        f"options pricing an event (IV/HV={median_iv / hv20:.2f}, "
        f"IV +{(median_iv / prev_iv - 1) * 100:.0f}% d/d)"
      )

  features = {
    "days_to_next_megacap_earnings": next_earnings,
    "days_to_next_macro_event": next_macro,
    "vix_5d_change": round(vix_5d_change, 4),
  }

  enabled = os.environ.get("TABFM_EVENT_GATE", "on").lower() != "off"
  return GateResult(
    gated=bool(reasons) and enabled,
    reasons=reasons,
    degraded=degraded,
    features=features,
  )
