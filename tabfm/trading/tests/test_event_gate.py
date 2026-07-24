from datetime import date
import json

from tabfm.trading.pipeline.event_gate import (
  evaluate_event_gate, load_macro_calendar, MEGA_CAPS, GateResult,
)

AS_OF = date(2026, 7, 24)  # a Friday
NO_EVENTS = {"earnings": []}
CALM_VIX = [["2026-07-17", 18.0], ["2026-07-20", 18.2], ["2026-07-21", 18.1],
            ["2026-07-22", 18.4], ["2026-07-23", 18.7], ["2026-07-24", 18.8]]
CALM_CHAIN = {"median_iv": 0.20, "hv20": 0.15, "prev_median_iv": 0.20}


def _gate(events=NO_EVENTS, macro=(), vix=CALM_VIX, chain=CALM_CHAIN, as_of=AS_OF):
  return evaluate_event_gate(events, list(macro), vix, chain, as_of)


def test_calm_day_not_gated():
  r = _gate()
  assert r.gated is False
  assert r.degraded is False


def test_megacap_earnings_today_gates():
  r = _gate(events={"earnings": [{"symbol": "GOOGL", "date": "2026-07-24", "when": "amc"}]})
  assert r.gated is True
  assert any("GOOGL" in x for x in r.reasons)


def test_megacap_earnings_next_session_gates_over_weekend():
  # Friday as_of -> next session is Monday 2026-07-27
  r = _gate(events={"earnings": [{"symbol": "NVDA", "date": "2026-07-27", "when": "bmo"}]})
  assert r.gated is True


def test_earnings_three_sessions_out_does_not_gate():
  r = _gate(events={"earnings": [{"symbol": "AAPL", "date": "2026-07-29", "when": "amc"}]})
  assert r.gated is False


def test_non_megacap_earnings_ignored():
  r = _gate(events={"earnings": [{"symbol": "KO", "date": "2026-07-24", "when": "amc"}]})
  assert r.gated is False


def test_macro_event_next_session_gates():
  r = _gate(macro=[{"date": "2026-07-27", "event": "FOMC rate decision"}])
  assert r.gated is True
  assert any("FOMC" in x for x in r.reasons)


def test_vix_spike_gates():
  spiky = [["2026-07-17", 16.0], ["2026-07-20", 16.5], ["2026-07-21", 17.0],
           ["2026-07-22", 17.5], ["2026-07-23", 18.0], ["2026-07-24", 18.6]]
  # (18.6 - 16.0) / 16.0 = +16.25% > 15%
  r = _gate(vix=spiky)
  assert r.gated is True
  assert any("VIX" in x for x in r.reasons)


def test_vix_below_threshold_does_not_gate():
  mild = [["2026-07-17", 17.0], ["2026-07-20", 17.2], ["2026-07-21", 17.5],
          ["2026-07-22", 17.8], ["2026-07-23", 18.5], ["2026-07-24", 19.3]]
  # +13.5% < 15%
  assert _gate(vix=mild).gated is False


def test_short_vix_history_skips_vix_check():
  assert _gate(vix=[["2026-07-24", 18.8]]).gated is False


def test_iv_spike_gates():
  chain = {"median_iv": 0.30, "hv20": 0.15, "prev_median_iv": 0.20}
  # ratio 2.0 > 1.6 and 0.30 > 0.20 * 1.2
  r = _gate(chain=chain)
  assert r.gated is True
  assert any("IV" in x for x in r.reasons)


def test_high_but_stable_iv_does_not_gate():
  chain = {"median_iv": 0.30, "hv20": 0.15, "prev_median_iv": 0.29}
  assert _gate(chain=chain).gated is False


def test_missing_prev_iv_disables_iv_check():
  chain = {"median_iv": 0.30, "hv20": 0.15, "prev_median_iv": None}
  assert _gate(chain=chain).gated is False


def test_degraded_when_events_none():
  r = _gate(events=None)
  assert r.degraded is True
  assert r.gated is False  # other layers still calm


def test_master_switch_disables_gating(monkeypatch):
  monkeypatch.setenv("TABFM_EVENT_GATE", "off")
  r = _gate(events={"earnings": [{"symbol": "GOOGL", "date": "2026-07-24", "when": "amc"}]})
  assert r.gated is False
  assert r.features["days_to_next_megacap_earnings"] == 0.0


def test_env_threshold_override(monkeypatch):
  monkeypatch.setenv("TABFM_GATE_VIX_5D", "0.10")
  mild = [["2026-07-17", 17.0], ["2026-07-20", 17.2], ["2026-07-21", 17.5],
          ["2026-07-22", 17.8], ["2026-07-23", 18.5], ["2026-07-24", 19.3]]
  assert _gate(vix=mild).gated is True  # +13.5% > overridden 10%


def test_features_computed():
  r = _gate(
    events={"earnings": [{"symbol": "META", "date": "2026-07-29", "when": "amc"}]},
    macro=[{"date": "2026-08-12", "event": "CPI release"}],
  )
  assert r.features["days_to_next_megacap_earnings"] == 3.0  # Fri->Wed = 3 busdays
  assert r.features["days_to_next_macro_event"] == 13.0
  assert round(r.features["vix_5d_change"], 4) == round((18.8 - 18.0) / 18.0, 4)


def test_feature_sentinel_when_no_events():
  r = _gate()
  assert r.features["days_to_next_megacap_earnings"] == 99.0
  assert r.features["days_to_next_macro_event"] == 99.0


def test_load_macro_calendar(tmp_path):
  p = tmp_path / "macro.json"
  p.write_text(json.dumps([{"date": "2026-07-29", "event": "FOMC rate decision"}]))
  cal = load_macro_calendar(p)
  assert cal[0]["event"] == "FOMC rate decision"


def test_load_macro_calendar_missing_file(tmp_path):
  assert load_macro_calendar(tmp_path / "nope.json") == []
