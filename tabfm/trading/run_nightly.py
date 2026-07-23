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
from .store.history_store import append_rows, label_expired_rows, compute_iv_rank, _DEFAULT_STORE
from .store.journal import _DEFAULT_DB


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
    rh.login(os.environ["RH_USER"], os.environ["RH_PASS"])
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

  closed = audit_positions(adapter, as_of, db_path)
  if closed:
    print(f"[PositionAuditor] Closed {len(closed)} position(s)")

  chain_data_list = fetch_chains(adapter, as_of)
  all_candidates = []
  all_feature_rows = []

  for chain_data in chain_data_list:
    iv_rank = compute_iv_rank(adapter.get_vix(as_of), store_path)
    feature_rows = engineer_features(chain_data, as_of, iv_rank)
    # Pre-filter: only passing rows go to TabFM; failing rows get fallback.
    passing = [r for r in feature_rows if _passes_filters(r)]
    failing = [r for r in feature_rows if not _passes_filters(r)]
    for row in failing:
      all_candidates.append({**row, "pop_predicted": 0.5, "exp_return": 0.0})
    # Batch score: group passing rows by regime, fit TabFM once per group.
    groups: dict[tuple, list[dict]] = {}
    for row in passing:
      key = (row["vix_bucket"], row["trend_direction"], row["iv_regime"])
      groups.setdefault(key, []).append(row)
    for group_rows in groups.values():
      context = build_context(group_rows[0], str(as_of), path=store_path)
      scored = score_candidates_batch(group_rows, context, clf_model, reg_model)
      all_candidates.extend(scored)
    all_feature_rows.extend(feature_rows)

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

  best = select_trade(all_candidates)
  if best is None:
    print("[TradeRecommender] No qualifying trade found today.")
    return None

  trade_id = execute_paper_trade(best, as_of, db_path)
  print(format_recommendation(best, trade_id, as_of))
  return best


if __name__ == "__main__":
  run()
