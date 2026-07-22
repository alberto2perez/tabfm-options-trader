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
from .pipeline.tabfm_scorer import score_candidate
from .pipeline.trade_recommender import select_trade
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
    from .adapters.live import LiveAdapter
    adapter = LiveAdapter()
  if clf_model is None:
    from tabfm import tabfm_v1_0_0_pytorch as tabfm_backend
    clf_model = tabfm_backend.load(model_type="classification")
  if reg_model is None:
    from tabfm import tabfm_v1_0_0_pytorch as tabfm_backend
    reg_model = tabfm_backend.load(model_type="regression")

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
    for row in feature_rows:
      context = build_context(row, str(as_of), path=store_path)
      scored = score_candidate(row, context, clf_model, reg_model)
      all_candidates.append(scored)
    all_feature_rows.extend(feature_rows)

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
