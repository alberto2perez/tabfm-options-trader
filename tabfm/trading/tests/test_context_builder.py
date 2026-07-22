import tempfile
from pathlib import Path
import pandas as pd
from tabfm.trading.pipeline.context_builder import build_context
from tabfm.trading.store.history_store import append_rows


def _make_rows(n: int, bucket="normal", trend="uptrend", iv_reg="fair") -> list[dict]:
  return [
    {"date": f"2025-01-{i+1:02d}", "vix_bucket": bucket, "trend_direction": trend,
     "iv_regime": iv_reg, "iv_rank": 50.0, "profitable": 1, "return_pct": 0.3}
    for i in range(n)
  ]


def test_build_context_exact_match(tmp_path):
  p = tmp_path / "store.parquet"
  append_rows(_make_rows(30, "normal", "uptrend", "fair"), p)
  result = build_context(
    {"vix_bucket": "normal", "trend_direction": "uptrend", "iv_regime": "fair"},
    "2026-01-01", n=60, path=p,
  )
  assert len(result) == 30


def test_build_context_caps_at_n(tmp_path):
  p = tmp_path / "store.parquet"
  append_rows(_make_rows(80, "normal", "uptrend", "fair"), p)
  result = build_context(
    {"vix_bucket": "normal", "trend_direction": "uptrend", "iv_regime": "fair"},
    "2026-01-01", n=60, path=p,
  )
  assert len(result) == 60


def test_build_context_fallback_when_sparse(tmp_path):
  p = tmp_path / "store.parquet"
  append_rows(_make_rows(5, "low", "sideways", "cheap"), p)
  result = build_context(
    {"vix_bucket": "spike", "trend_direction": "downtrend", "iv_regime": "expensive"},
    "2026-01-01", n=60, path=p,
  )
  assert len(result) == 5  # falls back to all available


def test_build_context_empty_when_no_store(tmp_path):
  p = tmp_path / "nonexistent.parquet"
  result = build_context(
    {"vix_bucket": "normal", "trend_direction": "uptrend", "iv_regime": "fair"},
    "2026-01-01", n=60, path=p,
  )
  assert result.empty
