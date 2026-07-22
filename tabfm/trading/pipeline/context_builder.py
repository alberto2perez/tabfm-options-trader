from pathlib import Path
import pandas as pd
from ..store.history_store import get_regime_rows, _DEFAULT_STORE


def build_context(
  features_row: dict,
  as_of_str: str,
  n: int = 60,
  path: Path = _DEFAULT_STORE,
) -> pd.DataFrame:
  """Select n regime-similar historical rows as TabFM context window."""
  return get_regime_rows(
    vix_bucket=features_row["vix_bucket"],
    trend_direction=features_row["trend_direction"],
    iv_regime=features_row["iv_regime"],
    before_date=as_of_str,
    n=n,
    path=path,
  )
