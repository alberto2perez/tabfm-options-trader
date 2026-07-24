"""Nightly pipeline entry point.

Usage (live):
  python -m tabfm.trading.run_nightly

Requires Robinhood auth — set RH_USER and RH_PASS in .env and run:
  python -c "import robin_stocks.robinhood as rh; rh.login('user', 'pass')"
once to cache credentials, or load via dotenv in this script.
"""
from datetime import date
from pathlib import Path

from .pipeline.chain_fetcher import fetch_chains
from .pipeline.feature_engineer import engineer_features
from .pipeline.context_builder import build_context
from .pipeline.tabfm_scorer import score_candidates_batch
from .pipeline.calibrator import fit_calibration, calibrate_pop
from .pipeline.trade_recommender import select_trade, _passes_filters
from .pipeline.paper_executor import execute_paper_trade, format_recommendation
from .pipeline.position_auditor import audit_positions
from .pipeline.portfolio import portfolio_summary
from .pipeline.event_gate import evaluate_event_gate, load_macro_calendar
from .store.history_store import append_rows, label_expired_rows, compute_iv_rank, _DEFAULT_STORE
from .store.journal import init_db, get_open_trades, _DEFAULT_DB
from .store.market_history import record_market_day, load_market_history


def run(
  adapter=None,
  clf_model=None,
  reg_model=None,
  as_of: date | None = None,
  db_path: Path = _DEFAULT_DB,
  store_path: Path = _DEFAULT_STORE,
) -> dict | None:
  if as_of is None:
    as_of = date.today()
  if adapter is None:
    import os
    import robin_stocks.robinhood as rh
    try:
      from dotenv import load_dotenv
      load_dotenv()
    except ImportError:
      pass
    user, pw = os.environ.get("RH_USER"), os.environ.get("RH_PASS")
    if user and pw:
      rh.login(user, pw)
    else:
      rh.login()  # cached session from a prior interactive login
    from .adapters.live import LiveAdapter
    adapter = LiveAdapter()
  if clf_model is None or reg_model is None:
    import torch
    from tabfm import tabfm_v1_0_0_pytorch as tabfm_backend
    device = "mps" if torch.backends.mps.is_available() else "cpu"
    if clf_model is None:
      clf_model = tabfm_backend.load(model_type="classification", device=device)
    if reg_model is None:
      reg_model = tabfm_backend.load(model_type="regression", device=device)

  print(f"[NightlyPipeline] {as_of}")

  init_db(db_path)
  closed = audit_positions(adapter, as_of, db_path)
  if closed:
    print(f"[PositionAuditor] Closed {len(closed)} position(s)")

  chain_data_list = fetch_chains(adapter, as_of)

  # --- Event risk gate -------------------------------------------------
  data_dir = Path(db_path).parent
  vix_now = adapter.get_vix(as_of)
  spy_chain = next(
    (c for c in chain_data_list if c["ticker"] == "SPY"),
    chain_data_list[0] if chain_data_list else None,
  )
  chain_stats = {}
  if spy_chain is not None and len(spy_chain["chain"]):
    prior_rows = [r for r in load_market_history(data_dir, n=2) if r["date"] < str(as_of)]
    prior_iv = prior_rows[-1]["median_iv"] if prior_rows else None
    chain_stats = {
      "median_iv": float(spy_chain["chain"]["iv"].median()),
      "hv20": float(spy_chain["underlying"]["hv20"]),
      "prev_median_iv": prior_iv,
    }
  vix_history = adapter.get_vix_history(as_of)
  if not vix_history:
    vix_history = [[h["date"], h["vix"]] for h in load_market_history(data_dir, n=10)]
  if not any(str(as_of) == str(d) for d, _ in vix_history):
    vix_history = vix_history + [[str(as_of), vix_now]]
  if getattr(adapter, "persists_market_history", True):
    record_market_day(data_dir, as_of, vix_now, chain_stats.get("median_iv"))

  gate = evaluate_event_gate(
    events=adapter.get_events(as_of),
    macro_calendar=load_macro_calendar(),
    vix_history=vix_history,
    chain_stats=chain_stats,
    as_of=as_of,
  )
  if gate.degraded:
    print("[EventGate] DEGRADED — earnings calendar unavailable")

  # --- Feature engineering (always runs, gated or not) -----------------
  all_candidates = []
  all_feature_rows = []
  scoring_groups: dict[tuple, list[dict]] = {}

  for chain_data in chain_data_list:
    iv_rank = compute_iv_rank(vix_now, store_path)
    hv20 = float(chain_data["underlying"]["hv20"]) or 0.0
    chain_df = chain_data["chain"]
    iv_spike = (
      float(chain_df["iv"].median()) / hv20
      if len(chain_df) and hv20 > 0 else 0.0
    )
    extra = {**gate.features, "iv_spike_score": round(iv_spike, 4)}
    feature_rows = engineer_features(chain_data, as_of, iv_rank, extra_features=extra)
    all_feature_rows.extend(feature_rows)
    if gate.gated:
      continue
    # Pre-filter: only passing rows go to TabFM; failing rows get fallback.
    for row in feature_rows:
      if not _passes_filters(row):
        all_candidates.append({**row, "pop_predicted": 0.5, "exp_return": 0.0})
        continue
      key = (row["vix_bucket"], row["trend_direction"], row["iv_regime"])
      scoring_groups.setdefault(key, []).append(row)

  # Batch score: fit TabFM once per regime group.
  for group_rows in scoring_groups.values():
    context = build_context(group_rows[0], str(as_of), path=store_path)
    scored = score_candidates_batch(group_rows, context, clf_model, reg_model)
    all_candidates.extend(scored)

  # Platt calibration: map raw POP% onto realized win rates from the journal.
  calib = fit_calibration(db_path)
  if calib is not None:
    for c in all_candidates:
      if c["pop_predicted"] == 0.5 and c["exp_return"] == 0.0:
        continue  # fallback-scored — nothing to calibrate
      c["pop_raw"] = c["pop_predicted"]
      c["pop_predicted"] = round(calibrate_pop(c["pop_raw"], calib), 4)

  append_rows(all_feature_rows, store_path)
  n_labeled = label_expired_rows(store_path, adapter, as_of)
  if n_labeled:
    print(f"[HistoryStore] Labeled {n_labeled} expired rows")

  if gate.gated:
    print(f"[EventGate] NO NEW ENTRIES — {'; '.join(gate.reasons)}")
    _log_gated_day(gate.reasons, as_of, db_path)
    print(portfolio_summary(db_path, as_of))
    return None

  best = select_trade(all_candidates, open_trades=get_open_trades(db_path))
  if best is None:
    print("[TradeRecommender] No qualifying trade found today.")
    print(portfolio_summary(db_path, as_of))
    return None

  trade_id = execute_paper_trade(best, as_of, db_path)
  rec_text = format_recommendation(best, trade_id, as_of)
  print(rec_text)
  _log_recommendation(rec_text, as_of, db_path)
  print(portfolio_summary(db_path, as_of))
  return best


def _log_gated_day(reasons: list, as_of: date, db_path: Path) -> None:
  md = Path(db_path).parent / "RECOMMENDATIONS.md"
  header = "# Nightly Recommendations\n\n"
  existing = ""
  if md.exists():
    existing = md.read_text()
    if existing.startswith(header):
      existing = existing[len(header):]
  bullets = "\n".join(f"- {r}" for r in reasons)
  entry = f"## {as_of}\n\nGATED — no new entries.\n{bullets}\n\n"
  md.write_text(header + entry + existing)


def _log_recommendation(rec_text: str, as_of: date, db_path: Path) -> None:
  """Prepend the recommendation to RECOMMENDATIONS.md next to the journal.

  Newest-first so the latest trade is at the top when read on GitHub.
  """
  md = Path(db_path).parent / "RECOMMENDATIONS.md"
  header = "# Nightly Recommendations\n\n"
  existing = ""
  if md.exists():
    existing = md.read_text()
    if existing.startswith(header):
      existing = existing[len(header):]
  entry = f"## {as_of}\n\n```\n{rec_text.strip()}\n```\n\n"
  md.write_text(header + entry + existing)


if __name__ == "__main__":
  run()
