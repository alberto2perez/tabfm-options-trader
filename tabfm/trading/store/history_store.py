from pathlib import Path
import pandas as pd

_DEFAULT_STORE = Path(__file__).parent.parent / "history_store.parquet"


def append_rows(rows: list[dict], path: Path = _DEFAULT_STORE) -> None:
  new_df = pd.DataFrame(rows)
  if path.exists():
    existing = pd.read_parquet(path)
    combined = pd.concat([existing, new_df], ignore_index=True)
  else:
    combined = new_df
  combined.to_parquet(path, index=False)


def load_store(path: Path = _DEFAULT_STORE) -> pd.DataFrame:
  if not path.exists():
    return pd.DataFrame()
  return pd.read_parquet(path)


def get_regime_rows(
  vix_bucket: str,
  trend_direction: str,
  iv_regime: str,
  before_date: str,
  n: int = 60,
  path: Path = _DEFAULT_STORE,
) -> pd.DataFrame:
  df = load_store(path)
  if df.empty:
    return df
  df = df[df["date"] < before_date].copy()
  if df.empty:
    return df

  # exact match
  exact = df[
    (df["vix_bucket"] == vix_bucket)
    & (df["trend_direction"] == trend_direction)
    & (df["iv_regime"] == iv_regime)
  ]
  if len(exact) >= n:
    return exact.tail(n)

  # relax iv_regime
  partial = df[
    (df["vix_bucket"] == vix_bucket) & (df["trend_direction"] == trend_direction)
  ]
  if len(partial) >= n:
    return partial.tail(n)

  # relax trend too
  bucket_only = df[df["vix_bucket"] == vix_bucket]
  if len(bucket_only) >= n:
    return bucket_only.tail(n)

  return df.tail(n)


def compute_iv_rank(current_vix: float, path: Path = _DEFAULT_STORE) -> float:
  df = load_store(path)
  if df.empty or "vix_level" not in df.columns or len(df) < 5:
    return 50.0
  hist = df["vix_level"].dropna()
  return float((hist < current_vix).mean() * 100)
