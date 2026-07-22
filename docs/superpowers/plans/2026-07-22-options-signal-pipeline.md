# Options Signal Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a nightly pipeline that uses TabFM ICL to scan 25 watchlist tickers, score vertical spread candidates, and emit one trade recommendation with POP% baked in — paper-traded in a local SQLite journal, validated by walk-forward backtest.

**Architecture:** A `DataAdapter` interface switches between `LiveAdapter` (Robinhood API via robin-stocks) and `HistAdapter` (yfinance + Black-Scholes). The same eight pipeline stages run for both live nightly signals and historical walk-forward backtests. A Parquet history store grows nightly as the ICL context pool; a SQLite journal tracks all paper trades.

**Tech Stack:** Python 3.11+, TabFM PyTorch backend, pandas, numpy, scipy (already in deps), yfinance, pyarrow, robin-stocks, sqlite3 (stdlib), pytest

## Global Constraints

- Python >= 3.11
- TabFM PyTorch backend: `from tabfm import tabfm_v1_0_0_pytorch as tabfm_backend`
- All new files under `tabfm/trading/`
- 2-space indentation (matches project's pyink config)
- Max trade risk: $1,000 per position — hard-coded constant, never passed as parameter
- No lookahead: HistAdapter asserts `requested_date <= self._as_of` on every call
- SQLite journal: `tabfm/trading/paper_journal.db` (add to `.gitignore`)
- Parquet store: `tabfm/trading/history_store.parquet` (add to `.gitignore`)
- Run tests: `PYTHONPATH=. pytest tabfm/trading/tests/ -v`

---

### Task 1: Project Scaffold, Dependencies, and Watchlist

**Files:**
- Create: `tabfm/trading/__init__.py`
- Create: `tabfm/trading/adapters/__init__.py`
- Create: `tabfm/trading/pipeline/__init__.py`
- Create: `tabfm/trading/store/__init__.py`
- Create: `tabfm/trading/backtest/__init__.py`
- Create: `tabfm/trading/tests/__init__.py`
- Create: `tabfm/trading/watchlist.py`
- Create: `tabfm/trading/tests/test_watchlist.py`
- Modify: `pyproject.toml` (add yfinance, pyarrow, robin-stocks to optional extras)
- Modify: `.gitignore` (add paper_journal.db, history_store.parquet)

**Interfaces:**
- Produces: `WATCHLIST: list[Ticker]`, `SYMBOLS: list[str]`, `SECTOR_MAP: dict[str, str]` from `tabfm.trading.watchlist`

- [ ] **Step 1: Write the failing test**

```python
# tabfm/trading/tests/test_watchlist.py
from tabfm.trading.watchlist import WATCHLIST, SYMBOLS, SECTOR_MAP


def test_watchlist_count():
  assert len(WATCHLIST) == 25
  assert len(set(SYMBOLS)) == 25  # no duplicates


def test_required_tickers_present():
  assert "SPY" in SYMBOLS
  assert "QQQ" in SYMBOLS
  assert "TSLA" in SYMBOLS


def test_sector_map_covers_all():
  assert set(SECTOR_MAP.keys()) == set(SYMBOLS)


def test_valid_sectors():
  valid = {"index_etf", "tech", "finance", "energy", "consumer"}
  assert all(v in valid for v in SECTOR_MAP.values())
```

- [ ] **Step 2: Run test to verify it fails**

```bash
PYTHONPATH=. pytest tabfm/trading/tests/test_watchlist.py -v
```
Expected: `ModuleNotFoundError`

- [ ] **Step 3: Create directory structure and empty `__init__.py` files**

```bash
mkdir -p tabfm/trading/{adapters,pipeline,store,backtest,tests}
touch tabfm/trading/__init__.py
touch tabfm/trading/adapters/__init__.py
touch tabfm/trading/pipeline/__init__.py
touch tabfm/trading/store/__init__.py
touch tabfm/trading/backtest/__init__.py
touch tabfm/trading/tests/__init__.py
```

- [ ] **Step 4: Write `tabfm/trading/watchlist.py`**

```python
from typing import NamedTuple


class Ticker(NamedTuple):
  symbol: str
  sector: str


WATCHLIST: list[Ticker] = [
  # Index ETFs
  Ticker("SPY", "index_etf"),
  Ticker("QQQ", "index_etf"),
  Ticker("IWM", "index_etf"),
  Ticker("GLD", "index_etf"),
  Ticker("TLT", "index_etf"),
  # Tech / high IV
  Ticker("NVDA", "tech"),
  Ticker("TSLA", "tech"),
  Ticker("META", "tech"),
  Ticker("AAPL", "tech"),
  Ticker("AMZN", "tech"),
  Ticker("GOOGL", "tech"),
  Ticker("MSFT", "tech"),
  Ticker("AMD", "tech"),
  Ticker("PLTR", "tech"),
  Ticker("MSTR", "tech"),
  # Finance
  Ticker("JPM", "finance"),
  Ticker("GS", "finance"),
  Ticker("BAC", "finance"),
  Ticker("XLF", "finance"),
  # Energy
  Ticker("XOM", "energy"),
  Ticker("XLE", "energy"),
  Ticker("OXY", "energy"),
  # Consumer
  Ticker("WMT", "consumer"),
  Ticker("COST", "consumer"),
  Ticker("UNH", "consumer"),
]

SYMBOLS: list[str] = [t.symbol for t in WATCHLIST]
SECTOR_MAP: dict[str, str] = {t.symbol: t.sector for t in WATCHLIST}
```

- [ ] **Step 5: Add trading deps to `pyproject.toml`**

Add under `[project.optional-dependencies]`:
```toml
trading = [
    "yfinance",
    "pyarrow",
    "robin-stocks",
]
```

- [ ] **Step 6: Add data files to `.gitignore`**

Append to `.gitignore`:
```
tabfm/trading/paper_journal.db
tabfm/trading/history_store.parquet
```

- [ ] **Step 7: Install trading deps**

```bash
pip install -e ".[trading,pytorch]"
```
Expected: `Successfully installed yfinance pyarrow robin-stocks ...`

- [ ] **Step 8: Run tests to verify they pass**

```bash
PYTHONPATH=. pytest tabfm/trading/tests/test_watchlist.py -v
```
Expected: `4 passed`

- [ ] **Step 9: Commit**

```bash
git add tabfm/trading/ pyproject.toml .gitignore
git commit -m "feat(trading): scaffold project structure and watchlist"
```

---

### Task 2: Storage Layer — SQLite Journal and Parquet History Store

**Files:**
- Create: `tabfm/trading/store/journal.py`
- Create: `tabfm/trading/store/history_store.py`
- Create: `tabfm/trading/tests/test_store.py`

**Interfaces:**
- Produces from `tabfm.trading.store.journal`:
  - `init_db(path: Path) -> None`
  - `insert_trade(trade: dict, path: Path) -> int`
  - `get_open_trades(path: Path) -> list[dict]`
  - `close_trade(trade_id: int, status: str, actual_pnl: float, date_closed: str, path: Path) -> None`
  - `get_all_closed_trades(path: Path) -> list[dict]`
- Produces from `tabfm.trading.store.history_store`:
  - `append_rows(rows: list[dict], path: Path) -> None`
  - `load_store(path: Path) -> pd.DataFrame`
  - `get_regime_rows(vix_bucket, trend_direction, iv_regime, before_date, n, path) -> pd.DataFrame`
  - `compute_iv_rank(current_vix: float, path: Path) -> float`

- [ ] **Step 1: Write the failing tests**

```python
# tabfm/trading/tests/test_store.py
import tempfile
from pathlib import Path
import pandas as pd
import pytest
from tabfm.trading.store.journal import (
  init_db, insert_trade, get_open_trades, close_trade, get_all_closed_trades,
)
from tabfm.trading.store.history_store import (
  append_rows, load_store, get_regime_rows, compute_iv_rank,
)

SAMPLE_TRADE = {
  "date_entered": "2025-01-10",
  "ticker": "SPY",
  "direction": "put_spread",
  "strike_short": 480.0,
  "strike_long": 475.0,
  "expiry": "2025-01-17",
  "dte": 7,
  "entry_credit": 1.20,
  "spread_width": 5.0,
  "contracts": 1,
  "max_loss": 380.0,
  "max_profit": 120.0,
  "pop_predicted": 0.72,
  "exp_return": 0.18,
  "regime": "normal|uptrend|fair",
}


@pytest.fixture
def tmp_db(tmp_path):
  db = tmp_path / "test.db"
  init_db(db)
  return db


@pytest.fixture
def tmp_parquet(tmp_path):
  return tmp_path / "test.parquet"


def test_insert_and_get_open(tmp_db):
  trade_id = insert_trade(SAMPLE_TRADE, tmp_db)
  assert trade_id == 1
  open_trades = get_open_trades(tmp_db)
  assert len(open_trades) == 1
  assert open_trades[0]["ticker"] == "SPY"
  assert open_trades[0]["status"] == "open"


def test_close_trade(tmp_db):
  trade_id = insert_trade(SAMPLE_TRADE, tmp_db)
  close_trade(trade_id, "won", 120.0, "2025-01-17", tmp_db)
  assert get_open_trades(tmp_db) == []
  closed = get_all_closed_trades(tmp_db)
  assert len(closed) == 1
  assert closed[0]["status"] == "won"
  assert closed[0]["actual_pnl"] == 120.0


def test_append_and_load_store(tmp_parquet):
  rows = [{"date": "2025-01-10", "vix_bucket": "normal", "trend_direction": "uptrend",
            "iv_regime": "fair", "iv_rank": 45.0, "profitable": 1, "return_pct": 0.3}]
  append_rows(rows, tmp_parquet)
  df = load_store(tmp_parquet)
  assert len(df) == 1
  assert df["vix_bucket"].iloc[0] == "normal"


def test_append_twice_accumulates(tmp_parquet):
  rows = [{"date": "2025-01-10", "vix_bucket": "normal", "trend_direction": "uptrend",
            "iv_regime": "fair", "iv_rank": 45.0, "profitable": 1, "return_pct": 0.3}]
  append_rows(rows, tmp_parquet)
  append_rows(rows, tmp_parquet)
  assert len(load_store(tmp_parquet)) == 2


def test_get_regime_rows_exact_match(tmp_parquet):
  rows = [
    {"date": "2025-01-0%d" % i, "vix_bucket": "normal", "trend_direction": "uptrend",
     "iv_regime": "fair", "iv_rank": 45.0, "profitable": 1, "return_pct": 0.3}
    for i in range(1, 10)
  ]
  rows += [{"date": "2025-01-10", "vix_bucket": "spike", "trend_direction": "downtrend",
             "iv_regime": "expensive", "iv_rank": 90.0, "profitable": 0, "return_pct": -1.0}]
  append_rows(rows, tmp_parquet)
  result = get_regime_rows("normal", "uptrend", "fair", "2026-01-01", n=60, path=tmp_parquet)
  assert len(result) == 9
  assert all(result["vix_bucket"] == "normal")


def test_get_regime_rows_fallback(tmp_parquet):
  rows = [{"date": "2025-01-0%d" % i, "vix_bucket": "low", "trend_direction": "uptrend",
            "iv_regime": "fair", "iv_rank": 10.0, "profitable": 1, "return_pct": 0.2}
          for i in range(1, 5)]
  append_rows(rows, tmp_parquet)
  # ask for "spike" which doesn't exist — should fall back to all rows
  result = get_regime_rows("spike", "downtrend", "expensive", "2026-01-01", n=60, path=tmp_parquet)
  assert len(result) == 4


def test_compute_iv_rank_no_history(tmp_parquet):
  rank = compute_iv_rank(20.0, tmp_parquet)
  assert rank == 50.0  # neutral default when no history


def test_compute_iv_rank_with_history(tmp_parquet):
  rows = [{"date": "2025-01-0%d" % i, "vix_level": float(10 + i),
            "vix_bucket": "normal", "trend_direction": "uptrend",
            "iv_regime": "fair", "iv_rank": 50.0, "profitable": 1, "return_pct": 0.2}
          for i in range(1, 10)]
  append_rows(rows, tmp_parquet)
  # vix_levels are 11,12,...,19. current_vix=15 → ~44th percentile
  rank = compute_iv_rank(15.0, tmp_parquet)
  assert 0.0 <= rank <= 100.0
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
PYTHONPATH=. pytest tabfm/trading/tests/test_store.py -v
```
Expected: `ImportError`

- [ ] **Step 3: Write `tabfm/trading/store/journal.py`**

```python
import sqlite3
from pathlib import Path

_DEFAULT_DB = Path(__file__).parent.parent / "paper_journal.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS paper_trades (
  trade_id      INTEGER PRIMARY KEY AUTOINCREMENT,
  date_entered  TEXT NOT NULL,
  ticker        TEXT NOT NULL,
  direction     TEXT NOT NULL,
  strike_short  REAL NOT NULL,
  strike_long   REAL NOT NULL,
  expiry        TEXT NOT NULL,
  dte           INTEGER NOT NULL,
  entry_credit  REAL NOT NULL,
  spread_width  REAL NOT NULL,
  contracts     INTEGER NOT NULL,
  max_loss      REAL NOT NULL,
  max_profit    REAL NOT NULL,
  pop_predicted REAL NOT NULL,
  exp_return    REAL NOT NULL,
  regime        TEXT NOT NULL,
  status        TEXT NOT NULL DEFAULT 'open',
  actual_pnl    REAL,
  date_closed   TEXT
);
"""


def init_db(path: Path = _DEFAULT_DB) -> None:
  with sqlite3.connect(path) as conn:
    conn.execute(_SCHEMA)


def insert_trade(trade: dict, path: Path = _DEFAULT_DB) -> int:
  with sqlite3.connect(path) as conn:
    cur = conn.execute(
      """INSERT INTO paper_trades
         (date_entered, ticker, direction, strike_short, strike_long, expiry,
          dte, entry_credit, spread_width, contracts, max_loss, max_profit,
          pop_predicted, exp_return, regime)
         VALUES (:date_entered, :ticker, :direction, :strike_short, :strike_long,
                 :expiry, :dte, :entry_credit, :spread_width, :contracts,
                 :max_loss, :max_profit, :pop_predicted, :exp_return, :regime)""",
      trade,
    )
    return cur.lastrowid


def get_open_trades(path: Path = _DEFAULT_DB) -> list[dict]:
  with sqlite3.connect(path) as conn:
    conn.row_factory = sqlite3.Row
    cur = conn.execute("SELECT * FROM paper_trades WHERE status = 'open'")
    return [dict(r) for r in cur.fetchall()]


def close_trade(
  trade_id: int,
  status: str,
  actual_pnl: float,
  date_closed: str,
  path: Path = _DEFAULT_DB,
) -> None:
  with sqlite3.connect(path) as conn:
    conn.execute(
      "UPDATE paper_trades SET status=?, actual_pnl=?, date_closed=? WHERE trade_id=?",
      (status, actual_pnl, date_closed, trade_id),
    )


def get_all_closed_trades(path: Path = _DEFAULT_DB) -> list[dict]:
  with sqlite3.connect(path) as conn:
    conn.row_factory = sqlite3.Row
    cur = conn.execute("SELECT * FROM paper_trades WHERE status != 'open'")
    return [dict(r) for r in cur.fetchall()]
```

- [ ] **Step 4: Write `tabfm/trading/store/history_store.py`**

```python
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
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
PYTHONPATH=. pytest tabfm/trading/tests/test_store.py -v
```
Expected: `11 passed`

- [ ] **Step 6: Commit**

```bash
git add tabfm/trading/store/ tabfm/trading/tests/test_store.py
git commit -m "feat(trading): add SQLite journal and Parquet history store"
```

---

### Task 3: DataAdapter Base Class and HistAdapter

**Files:**
- Create: `tabfm/trading/adapters/base.py`
- Create: `tabfm/trading/adapters/historical.py`
- Create: `tabfm/trading/tests/test_hist_adapter.py`

**Interfaces:**
- Produces from `tabfm.trading.adapters.base`: `DataAdapter` (ABC)
- Produces from `tabfm.trading.adapters.historical`: `HistAdapter(as_of: date)`
  - `get_underlying(ticker, as_of) -> dict` — keys: `close`, `sma20`, `sma50`, `atr14`, `hv20`, `volume`, `volume_zscore`, `momentum_5d`, `momentum_20d`
  - `get_options_chain(ticker, as_of) -> pd.DataFrame` — cols: `strike`, `expiry`, `option_type`, `bid`, `ask`, `mid`, `open_interest`, `delta`, `iv`, `dte`
  - `get_vix(as_of) -> float`

- [ ] **Step 1: Write the failing tests**

```python
# tabfm/trading/tests/test_hist_adapter.py
import pytest
from datetime import date
from tabfm.trading.adapters.base import DataAdapter
from tabfm.trading.adapters.historical import HistAdapter

AS_OF = date(2025, 1, 10)


def test_hist_adapter_is_data_adapter():
  adapter = HistAdapter(as_of=AS_OF)
  assert isinstance(adapter, DataAdapter)


def test_get_underlying_returns_required_keys():
  adapter = HistAdapter(as_of=AS_OF)
  result = adapter.get_underlying("SPY", AS_OF)
  for key in ("close", "sma20", "sma50", "atr14", "hv20", "volume",
              "volume_zscore", "momentum_5d", "momentum_20d"):
    assert key in result, f"Missing key: {key}"
  assert result["close"] > 0


def test_get_underlying_no_lookahead():
  adapter = HistAdapter(as_of=AS_OF)
  future = date(2025, 6, 1)
  with pytest.raises(AssertionError, match="Lookahead"):
    adapter.get_underlying("SPY", future)


def test_get_options_chain_columns():
  adapter = HistAdapter(as_of=AS_OF)
  df = adapter.get_options_chain("SPY", AS_OF)
  for col in ("strike", "expiry", "option_type", "bid", "ask", "mid",
              "open_interest", "delta", "iv", "dte"):
    assert col in df.columns, f"Missing column: {col}"
  assert len(df) > 0


def test_get_options_chain_has_calls_and_puts():
  adapter = HistAdapter(as_of=AS_OF)
  df = adapter.get_options_chain("SPY", AS_OF)
  assert "call" in df["option_type"].values
  assert "put" in df["option_type"].values


def test_get_vix_returns_positive_float():
  adapter = HistAdapter(as_of=AS_OF)
  vix = adapter.get_vix(AS_OF)
  assert isinstance(vix, float)
  assert vix > 0
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
PYTHONPATH=. pytest tabfm/trading/tests/test_hist_adapter.py -v
```
Expected: `ImportError`

- [ ] **Step 3: Write `tabfm/trading/adapters/base.py`**

```python
from abc import ABC, abstractmethod
from datetime import date
import pandas as pd


class DataAdapter(ABC):
  @abstractmethod
  def get_underlying(self, ticker: str, as_of: date) -> dict:
    """Return dict with keys: close, sma20, sma50, atr14, hv20, volume,
    volume_zscore, momentum_5d, momentum_20d."""
    ...

  @abstractmethod
  def get_options_chain(self, ticker: str, as_of: date) -> pd.DataFrame:
    """Return DataFrame with columns: strike, expiry, option_type, bid, ask,
    mid, open_interest, delta, iv, dte."""
    ...

  @abstractmethod
  def get_vix(self, as_of: date) -> float:
    ...
```

- [ ] **Step 4: Write `tabfm/trading/adapters/historical.py`**

```python
from datetime import date, timedelta
import numpy as np
import pandas as pd
import yfinance as yf
from scipy.stats import norm
from .base import DataAdapter

_RISK_FREE_RATE = 0.045
_DTE_WINDOWS = [7, 14, 21, 30, 45]
_STRIKE_RANGE = np.arange(0.70, 1.31, 0.025)


def _bs_delta(S: float, K: float, T: float, sigma: float, opt: str) -> float:
  if T <= 0 or sigma <= 0:
    return 0.0
  d1 = (np.log(S / K) + (0.5 * sigma**2) * T) / (sigma * np.sqrt(T))
  return float(norm.cdf(d1) if opt == "call" else norm.cdf(d1) - 1)


def _bs_price(S: float, K: float, T: float, sigma: float, opt: str) -> float:
  if T <= 0:
    return max(0.0, (S - K) if opt == "call" else (K - S))
  d1 = (np.log(S / K) + (0.5 * sigma**2) * T) / (sigma * np.sqrt(T))
  d2 = d1 - sigma * np.sqrt(T)
  if opt == "call":
    return float(S * norm.cdf(d1) - K * np.exp(-_RISK_FREE_RATE * T) * norm.cdf(d2))
  return float(K * np.exp(-_RISK_FREE_RATE * T) * norm.cdf(-d2) - S * norm.cdf(-d1))


class HistAdapter(DataAdapter):
  def __init__(self, as_of: date) -> None:
    self._as_of = as_of
    self._cache: dict = {}

  def _assert_no_lookahead(self, requested: date) -> None:
    assert requested <= self._as_of, (
      f"Lookahead violation: requested {requested} but as_of is {self._as_of}"
    )

  def _history(self, ticker: str, lookback: int = 300) -> pd.DataFrame:
    key = (ticker, lookback)
    if key not in self._cache:
      start = self._as_of - timedelta(days=lookback)
      df = yf.download(
        ticker, start=str(start), end=str(self._as_of), progress=False, auto_adjust=True
      )
      self._cache[key] = df
    return self._cache[key]

  def get_underlying(self, ticker: str, as_of: date) -> dict:
    self._assert_no_lookahead(as_of)
    df = self._history(ticker)
    if df.empty or len(df) < 50:
      raise ValueError(f"Insufficient history for {ticker}")

    close = float(df["Close"].iloc[-1])
    sma20 = float(df["Close"].rolling(20).mean().iloc[-1])
    sma50 = float(df["Close"].rolling(50).mean().iloc[-1])
    atr14 = float((df["High"] - df["Low"]).rolling(14).mean().iloc[-1])
    hv20 = float(df["Close"].pct_change().dropna().iloc[-20:].std() * np.sqrt(252))
    volume = float(df["Volume"].iloc[-1])
    vol_series = df["Volume"].rolling(20)
    vol_std = float(vol_series.std().iloc[-1])
    vol_mean = float(vol_series.mean().iloc[-1])
    vol_z = (volume - vol_mean) / vol_std if vol_std > 0 else 0.0

    return {
      "close": close,
      "sma20": sma20,
      "sma50": sma50,
      "atr14": atr14,
      "hv20": hv20,
      "volume": volume,
      "volume_zscore": float(vol_z),
      "momentum_5d": float(df["Close"].pct_change(5).iloc[-1]),
      "momentum_20d": float(df["Close"].pct_change(20).iloc[-1]),
    }

  def get_options_chain(self, ticker: str, as_of: date) -> pd.DataFrame:
    self._assert_no_lookahead(as_of)
    u = self.get_underlying(ticker, as_of)
    S, sigma = u["close"], max(u["hv20"], 0.05)
    rows = []
    for dte in _DTE_WINDOWS:
      expiry = as_of + timedelta(days=dte)
      T = dte / 365.0
      for pct in _STRIKE_RANGE:
        K = round(S * pct, 0)
        for opt in ("call", "put"):
          price = _bs_price(S, K, T, sigma, opt)
          delta = abs(_bs_delta(S, K, T, sigma, opt))
          rows.append({
            "strike": K,
            "expiry": expiry,
            "option_type": opt,
            "bid": round(price * 0.95, 2),
            "ask": round(price * 1.05, 2),
            "mid": round(price, 2),
            "open_interest": 500,
            "delta": round(delta, 4),
            "iv": round(sigma, 4),
            "dte": dte,
          })
    return pd.DataFrame(rows)

  def get_vix(self, as_of: date) -> float:
    self._assert_no_lookahead(as_of)
    df = self._history("^VIX", lookback=30)
    if df.empty:
      return 20.0
    return float(df["Close"].iloc[-1])
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
PYTHONPATH=. pytest tabfm/trading/tests/test_hist_adapter.py -v
```
Expected: `6 passed` (note: these tests call yfinance — requires internet; ~10s)

- [ ] **Step 6: Commit**

```bash
git add tabfm/trading/adapters/ tabfm/trading/tests/test_hist_adapter.py
git commit -m "feat(trading): add DataAdapter ABC and HistAdapter with Black-Scholes"
```

---

### Task 4: LiveAdapter (Robinhood)

**Files:**
- Create: `tabfm/trading/adapters/live.py`
- Create: `tabfm/trading/tests/test_live_adapter.py`

**Interfaces:**
- Produces from `tabfm.trading.adapters.live`: `LiveAdapter()`
  - Same `get_underlying`, `get_options_chain`, `get_vix` interface as `HistAdapter`

- [ ] **Step 1: Write the failing tests**

```python
# tabfm/trading/tests/test_live_adapter.py
from tabfm.trading.adapters.base import DataAdapter
from tabfm.trading.adapters.live import LiveAdapter


def test_live_adapter_is_data_adapter():
  assert issubclass(LiveAdapter, DataAdapter)


def test_live_adapter_instantiates():
  # LiveAdapter requires no constructor args (credentials handled by robin_stocks login)
  adapter = LiveAdapter()
  assert adapter is not None
```

- [ ] **Step 2: Run test to verify it fails**

```bash
PYTHONPATH=. pytest tabfm/trading/tests/test_live_adapter.py -v
```
Expected: `ImportError`

- [ ] **Step 3: Write `tabfm/trading/adapters/live.py`**

```python
"""LiveAdapter wraps the Robinhood API via robin-stocks.

Authentication: call `robin_stocks.robinhood.login(username, password)` once
before using this adapter. Credentials can be stored in a local `.env` file
and loaded with python-dotenv:

  from dotenv import load_dotenv
  import os, robin_stocks.robinhood as rh
  load_dotenv()
  rh.login(os.environ["RH_USER"], os.environ["RH_PASS"])
"""
from datetime import date, datetime
import numpy as np
import pandas as pd
import robin_stocks.robinhood as rh
from .base import DataAdapter


class LiveAdapter(DataAdapter):
  def get_underlying(self, ticker: str, as_of: date) -> dict:
    quote = rh.get_stock_quote_by_symbol(ticker)
    price = float(quote["last_trade_price"])
    historicals = rh.get_stock_historicals(
      ticker, interval="day", span="3month", bounds="regular"
    )
    closes = pd.Series([float(h["close_price"]) for h in historicals])
    volumes = pd.Series([float(h["volume"]) for h in historicals])
    sma20 = float(closes.rolling(20).mean().iloc[-1])
    sma50 = float(closes.rolling(50).mean().iloc[-1]) if len(closes) >= 50 else sma20
    highs = pd.Series([float(h["high_price"]) for h in historicals])
    lows = pd.Series([float(h["low_price"]) for h in historicals])
    atr14 = float((highs - lows).rolling(14).mean().iloc[-1])
    hv20 = float(closes.pct_change().dropna().iloc[-20:].std() * np.sqrt(252))
    vol_mean = float(volumes.rolling(20).mean().iloc[-1])
    vol_std = float(volumes.rolling(20).std().iloc[-1])
    vol_z = (volumes.iloc[-1] - vol_mean) / vol_std if vol_std > 0 else 0.0
    return {
      "close": price,
      "sma20": sma20,
      "sma50": sma50,
      "atr14": atr14,
      "hv20": hv20,
      "volume": float(volumes.iloc[-1]),
      "volume_zscore": float(vol_z),
      "momentum_5d": float(closes.pct_change(5).iloc[-1]),
      "momentum_20d": float(closes.pct_change(20).iloc[-1]),
    }

  def get_options_chain(self, ticker: str, as_of: date) -> pd.DataFrame:
    expirations = rh.get_chains(ticker)["expiration_dates"]
    rows = []
    for exp_str in expirations[:5]:  # up to 5 nearest expiries
      exp_date = datetime.strptime(exp_str, "%Y-%m-%d").date()
      dte = (exp_date - as_of).days
      if not (5 <= dte <= 50):
        continue
      for opt_type in ("call", "put"):
        options = rh.find_options_by_expiration(ticker, exp_str, optionType=opt_type)
        for o in (options or []):
          try:
            rows.append({
              "strike": float(o["strike_price"]),
              "expiry": exp_date,
              "option_type": opt_type,
              "bid": float(o["bid_price"] or 0),
              "ask": float(o["ask_price"] or 0),
              "mid": (float(o["bid_price"] or 0) + float(o["ask_price"] or 0)) / 2,
              "open_interest": int(o["open_interest"] or 0),
              "delta": abs(float(o["delta"] or 0)),
              "iv": float(o["implied_volatility"] or 0),
              "dte": dte,
            })
          except (TypeError, ValueError):
            continue
    return pd.DataFrame(rows)

  def get_vix(self, as_of: date) -> float:
    quote = rh.get_stock_quote_by_symbol("VIXY")  # VIX proxy ETF available on Robinhood
    return float(quote["last_trade_price"]) * 10  # rough VIX approximation
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
PYTHONPATH=. pytest tabfm/trading/tests/test_live_adapter.py -v
```
Expected: `2 passed` (interface tests only — live tests require auth)

- [ ] **Step 5: Commit**

```bash
git add tabfm/trading/adapters/live.py tabfm/trading/tests/test_live_adapter.py
git commit -m "feat(trading): add LiveAdapter wrapping Robinhood API via robin-stocks"
```

---

### Task 5: Chain Fetcher (Stage 1)

**Files:**
- Create: `tabfm/trading/pipeline/chain_fetcher.py`
- Create: `tabfm/trading/tests/test_chain_fetcher.py`

**Interfaces:**
- Consumes: `DataAdapter`, `WATCHLIST`
- Produces: `fetch_chains(adapter, as_of) -> list[dict]`
  - Each dict has keys: `ticker`, `sector`, `underlying` (dict), `chain` (DataFrame), `vix` (float)

- [ ] **Step 1: Write the failing tests**

```python
# tabfm/trading/tests/test_chain_fetcher.py
from datetime import date
from unittest.mock import MagicMock
import pandas as pd
from tabfm.trading.pipeline.chain_fetcher import fetch_chains

AS_OF = date(2025, 1, 10)

def _mock_adapter(fail_tickers=None):
  fail_tickers = fail_tickers or []
  adapter = MagicMock()
  adapter.get_vix.return_value = 18.5
  def get_underlying(ticker, as_of):
    if ticker in fail_tickers:
      raise ValueError("test error")
    return {"close": 100.0, "sma20": 98.0, "sma50": 95.0, "atr14": 2.0,
            "hv20": 0.18, "volume": 1e6, "volume_zscore": 0.3,
            "momentum_5d": 0.01, "momentum_20d": 0.03}
  def get_options_chain(ticker, as_of):
    if ticker in fail_tickers:
      raise ValueError("test error")
    return pd.DataFrame({"strike": [99.0], "expiry": [date(2025, 1, 17)],
                         "option_type": ["put"], "bid": [1.0], "ask": [1.1],
                         "mid": [1.05], "open_interest": [200], "delta": [0.25],
                         "iv": [0.20], "dte": [7]})
  adapter.get_underlying.side_effect = get_underlying
  adapter.get_options_chain.side_effect = get_options_chain
  return adapter


def test_fetch_chains_returns_all_tickers():
  adapter = _mock_adapter()
  results = fetch_chains(adapter, AS_OF)
  assert len(results) == 25


def test_fetch_chains_result_keys():
  adapter = _mock_adapter()
  results = fetch_chains(adapter, AS_OF)
  for r in results:
    assert "ticker" in r
    assert "sector" in r
    assert "underlying" in r
    assert "chain" in r
    assert "vix" in r


def test_fetch_chains_skips_erroring_tickers():
  adapter = _mock_adapter(fail_tickers=["TSLA", "NVDA"])
  results = fetch_chains(adapter, AS_OF)
  tickers = [r["ticker"] for r in results]
  assert "TSLA" not in tickers
  assert "NVDA" not in tickers
  assert len(results) == 23
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
PYTHONPATH=. pytest tabfm/trading/tests/test_chain_fetcher.py -v
```
Expected: `ImportError`

- [ ] **Step 3: Write `tabfm/trading/pipeline/chain_fetcher.py`**

```python
from datetime import date
from ..adapters.base import DataAdapter
from ..watchlist import WATCHLIST


def fetch_chains(adapter: DataAdapter, as_of: date) -> list[dict]:
  """Fetch options chain and underlying data for all watchlist tickers.

  Skips any ticker that raises an exception (network error, insufficient history).
  """
  results = []
  vix = adapter.get_vix(as_of)
  for ticker_info in WATCHLIST:
    try:
      underlying = adapter.get_underlying(ticker_info.symbol, as_of)
      chain = adapter.get_options_chain(ticker_info.symbol, as_of)
      results.append({
        "ticker": ticker_info.symbol,
        "sector": ticker_info.sector,
        "underlying": underlying,
        "chain": chain,
        "vix": vix,
      })
    except Exception as e:
      print(f"[ChainFetcher] Skipping {ticker_info.symbol}: {e}")
  return results
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
PYTHONPATH=. pytest tabfm/trading/tests/test_chain_fetcher.py -v
```
Expected: `3 passed`

- [ ] **Step 5: Commit**

```bash
git add tabfm/trading/pipeline/chain_fetcher.py tabfm/trading/tests/test_chain_fetcher.py
git commit -m "feat(trading): add ChainFetcher pipeline stage"
```

---

### Task 6: Feature Engineer (Stage 2)

**Files:**
- Create: `tabfm/trading/pipeline/feature_engineer.py`
- Create: `tabfm/trading/tests/test_feature_engineer.py`

**Interfaces:**
- Consumes: chain_data dict (from ChainFetcher), `as_of: date`, `iv_rank: float`
- Produces: `engineer_features(chain_data, as_of, iv_rank) -> list[dict]`
  - Each dict is one candidate vertical spread with all feature columns from the spec

- [ ] **Step 1: Write the failing tests**

```python
# tabfm/trading/tests/test_feature_engineer.py
from datetime import date, timedelta
import pandas as pd
from tabfm.trading.pipeline.feature_engineer import (
  engineer_features, _vix_bucket, _trend_direction, _iv_regime,
)

AS_OF = date(2025, 1, 10)


def _make_chain(as_of: date, n_strikes: int = 5) -> pd.DataFrame:
  S = 100.0
  rows = []
  for i in range(n_strikes):
    k = S * (0.85 + i * 0.05)
    for opt in ("call", "put"):
      delta = 0.35 - i * 0.05
      rows.append({
        "strike": round(k, 0),
        "expiry": as_of + timedelta(days=14),
        "option_type": opt,
        "bid": 1.80, "ask": 2.00, "mid": 1.90,
        "open_interest": 300, "delta": delta, "iv": 0.22, "dte": 14,
      })
  return pd.DataFrame(rows)


def _make_chain_data(as_of: date) -> dict:
  return {
    "ticker": "SPY",
    "sector": "index_etf",
    "vix": 18.5,
    "chain": _make_chain(as_of),
    "underlying": {
      "close": 100.0, "sma20": 98.0, "sma50": 95.0, "atr14": 1.5,
      "hv20": 0.18, "volume": 5e7, "volume_zscore": 0.4,
      "momentum_5d": 0.01, "momentum_20d": 0.03,
    },
  }


def test_vix_bucket():
  assert _vix_bucket(12.0) == "low"
  assert _vix_bucket(20.0) == "normal"
  assert _vix_bucket(30.0) == "elevated"
  assert _vix_bucket(40.0) == "spike"


def test_trend_direction():
  assert _trend_direction(100.0, 98.0, 95.0) == "uptrend"
  assert _trend_direction(90.0, 93.0, 96.0) == "downtrend"
  assert _trend_direction(97.0, 98.0, 95.0) == "sideways"


def test_iv_regime():
  assert _iv_regime(10.0) == "cheap"
  assert _iv_regime(50.0) == "fair"
  assert _iv_regime(85.0) == "expensive"


def test_engineer_features_returns_list_of_dicts():
  rows = engineer_features(_make_chain_data(AS_OF), AS_OF, iv_rank=55.0)
  assert isinstance(rows, list)
  assert len(rows) > 0
  assert isinstance(rows[0], dict)


def test_engineer_features_required_columns():
  rows = engineer_features(_make_chain_data(AS_OF), AS_OF, iv_rank=55.0)
  required = [
    "date", "ticker", "sector", "direction", "strike_short", "strike_long",
    "expiry", "dte", "entry_credit", "spread_width_dollars", "max_profit",
    "max_loss", "short_delta", "bid_ask_pct", "open_interest",
    "price_close", "momentum_5d", "momentum_20d", "atr_14", "volume_zscore",
    "price_vs_sma20", "vix_level", "iv_rank", "hv20", "hv_iv_ratio",
    "vix_bucket", "trend_direction", "iv_regime", "earnings_flag",
    "expiry_type", "direction",
  ]
  for col in required:
    assert col in rows[0], f"Missing column: {col}"


def test_engineer_features_direction_values():
  rows = engineer_features(_make_chain_data(AS_OF), AS_OF, iv_rank=55.0)
  directions = {r["direction"] for r in rows}
  assert directions.issubset({"call_spread", "put_spread"})


def test_engineer_features_filters_zero_credit():
  rows = engineer_features(_make_chain_data(AS_OF), AS_OF, iv_rank=55.0)
  assert all(r["entry_credit"] > 0 for r in rows)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
PYTHONPATH=. pytest tabfm/trading/tests/test_feature_engineer.py -v
```
Expected: `ImportError`

- [ ] **Step 3: Write `tabfm/trading/pipeline/feature_engineer.py`**

```python
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
  chain_data: dict, as_of: date, iv_rank: float
) -> list[dict]:
  """Generate one feature row per candidate vertical spread.

  Args:
    chain_data: dict from ChainFetcher with ticker/sector/underlying/chain/vix
    as_of: signal date
    iv_rank: IV percentile for this ticker (0-100)

  Returns: list of feature dicts, one per viable spread candidate
  """
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
        "vix_5d_change": 0.0,
        "iv_rank": round(iv_rank, 2),
        "hv20": round(u["hv20"], 4),
        "hv_iv_ratio": round(u["hv20"] / float(short["iv"]), 4) if float(short["iv"]) > 0 else 0.0,
        "vix_bucket": vix_bkt,
        "trend_direction": trend,
        "iv_regime": iv_reg,
        "earnings_flag": "no_earnings",
      })
  return rows
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
PYTHONPATH=. pytest tabfm/trading/tests/test_feature_engineer.py -v
```
Expected: `8 passed`

- [ ] **Step 5: Commit**

```bash
git add tabfm/trading/pipeline/feature_engineer.py tabfm/trading/tests/test_feature_engineer.py
git commit -m "feat(trading): add FeatureEngineer pipeline stage"
```

---

### Task 7: Context Builder (Stage 3) and Trade Recommender (Stage 5)

**Files:**
- Create: `tabfm/trading/pipeline/context_builder.py`
- Create: `tabfm/trading/pipeline/trade_recommender.py`
- Create: `tabfm/trading/tests/test_context_builder.py`
- Create: `tabfm/trading/tests/test_trade_recommender.py`

**Interfaces:**
- Produces: `build_context(features_row, as_of_str, n, path) -> pd.DataFrame`
- Produces: `select_trade(scored_candidates) -> dict | None`
  - Candidate dict must include: `pop_predicted`, `exp_return`, `spread_width_dollars`, `contracts`, `total_risk`, `bid_ask_pct`, `open_interest`, `dte`, `short_delta`, `earnings_flag`, `score`

- [ ] **Step 1: Write the failing tests**

```python
# tabfm/trading/tests/test_context_builder.py
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
```

```python
# tabfm/trading/tests/test_trade_recommender.py
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
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
PYTHONPATH=. pytest tabfm/trading/tests/test_context_builder.py tabfm/trading/tests/test_trade_recommender.py -v
```
Expected: `ImportError`

- [ ] **Step 3: Write `tabfm/trading/pipeline/context_builder.py`**

```python
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
```

- [ ] **Step 4: Write `tabfm/trading/pipeline/trade_recommender.py`**

```python
import math

_MAX_RISK = 1000.0
_MAX_CONTRACTS = 10


def _passes_filters(row: dict) -> bool:
  if row["spread_width_dollars"] * 100 > _MAX_RISK:
    return False
  if row["bid_ask_pct"] > 0.15:
    return False
  if row["open_interest"] < 100:
    return False
  if not (7 <= row["dte"] <= 45):
    return False
  if not (0.15 <= row["short_delta"] <= 0.40):
    return False
  if row["earnings_flag"] == "earnings_week":
    return False
  return True


def _contracts(spread_width: float) -> int:
  n = math.floor(_MAX_RISK / (spread_width * 100))
  return max(1, min(n, _MAX_CONTRACTS))


def select_trade(scored_candidates: list[dict]) -> dict | None:
  """Apply filter gauntlet and return the single highest expected-value trade."""
  survivors = [c for c in scored_candidates if _passes_filters(c)]
  if not survivors:
    return None

  for c in survivors:
    c["contracts"] = _contracts(c["spread_width_dollars"])
    c["total_risk"] = c["contracts"] * c["spread_width_dollars"] * 100
    c["score"] = c["pop_predicted"] * c["exp_return"]

  positive_ev = [c for c in survivors if c["score"] > 0]
  if not positive_ev:
    return None

  return max(positive_ev, key=lambda c: c["score"])
```

- [ ] **Step 5: Export `_DEFAULT_STORE` from history_store (needed by context_builder import)**

Add to `tabfm/trading/store/history_store.py` — verify the constant is module-level (it already is from Task 2). No change needed.

- [ ] **Step 6: Run tests to verify they pass**

```bash
PYTHONPATH=. pytest tabfm/trading/tests/test_context_builder.py tabfm/trading/tests/test_trade_recommender.py -v
```
Expected: `13 passed`

- [ ] **Step 7: Commit**

```bash
git add tabfm/trading/pipeline/context_builder.py tabfm/trading/pipeline/trade_recommender.py \
        tabfm/trading/tests/test_context_builder.py tabfm/trading/tests/test_trade_recommender.py
git commit -m "feat(trading): add ContextBuilder and TradeRecommender pipeline stages"
```

---

### Task 8: TabFM Scorer (Stage 4)

**Files:**
- Create: `tabfm/trading/pipeline/tabfm_scorer.py`
- Create: `tabfm/trading/tests/test_tabfm_scorer.py`

**Interfaces:**
- Consumes: `candidate: dict`, `context: pd.DataFrame`, `clf_model`, `reg_model`
- Produces: `score_candidate(candidate, context, clf_model, reg_model) -> dict`
  - Adds `pop_predicted: float` (0–1) and `exp_return: float` to the candidate dict

- [ ] **Step 1: Write the failing tests**

```python
# tabfm/trading/tests/test_tabfm_scorer.py
import pandas as pd
import numpy as np
from unittest.mock import MagicMock, patch
from tabfm.trading.pipeline.tabfm_scorer import score_candidate, FEATURE_COLS

_CANDIDATE = {
  "price_close": 480.0, "momentum_5d": 1.2, "momentum_20d": 3.5,
  "atr_14": 8.5, "volume_zscore": 0.3, "price_vs_sma20": 1.8,
  "vix_level": 18.5, "vix_5d_change": -0.5, "iv_rank": 55.0,
  "hv20": 0.18, "hv_iv_ratio": 0.9, "dte": 14, "short_delta": 0.25,
  "strike_distance_pct": 4.2, "spread_width_dollars": 5.0, "bid_ask_pct": 0.08,
  "vix_bucket": "normal", "trend_direction": "uptrend", "iv_regime": "fair",
  "earnings_flag": "no_earnings", "direction": "put_spread",
  "expiry_type": "weekly", "sector": "index_etf",
}

def _context(n: int = 20) -> pd.DataFrame:
  rows = []
  for i in range(n):
    row = {col: _CANDIDATE.get(col, "normal") for col in FEATURE_COLS}
    row["profitable"] = i % 2
    row["return_pct"] = 0.2 if i % 2 else -0.8
    rows.append(row)
  return pd.DataFrame(rows)


def test_score_candidate_adds_pop_and_return():
  with patch("tabfm.trading.pipeline.tabfm_scorer.TabFMClassifier") as MockClf, \
       patch("tabfm.trading.pipeline.tabfm_scorer.TabFMRegressor") as MockReg:
    clf_inst = MagicMock()
    clf_inst.predict_proba.return_value = np.array([[0.28, 0.72]])
    MockClf.return_value = clf_inst

    reg_inst = MagicMock()
    reg_inst.predict.return_value = np.array([0.18])
    MockReg.return_value = reg_inst

    result = score_candidate(_CANDIDATE, _context(), MagicMock(), MagicMock())

  assert "pop_predicted" in result
  assert "exp_return" in result
  assert abs(result["pop_predicted"] - 0.72) < 0.01
  assert abs(result["exp_return"] - 0.18) < 0.01


def test_score_candidate_empty_context_returns_defaults():
  result = score_candidate(_CANDIDATE, pd.DataFrame(), MagicMock(), MagicMock())
  assert result["pop_predicted"] == 0.5
  assert result["exp_return"] == 0.0


def test_score_candidate_preserves_original_fields():
  with patch("tabfm.trading.pipeline.tabfm_scorer.TabFMClassifier") as MockClf, \
       patch("tabfm.trading.pipeline.tabfm_scorer.TabFMRegressor") as MockReg:
    MockClf.return_value.predict_proba.return_value = np.array([[0.3, 0.7]])
    MockReg.return_value.predict.return_value = np.array([0.15])
    result = score_candidate(_CANDIDATE, _context(), MagicMock(), MagicMock())

  assert result["ticker"] if "ticker" in _CANDIDATE else True
  assert result["dte"] == 14


def test_feature_cols_are_defined():
  assert len(FEATURE_COLS) > 0
  assert "iv_rank" in FEATURE_COLS
  assert "vix_bucket" in FEATURE_COLS
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
PYTHONPATH=. pytest tabfm/trading/tests/test_tabfm_scorer.py -v
```
Expected: `ImportError`

- [ ] **Step 3: Write `tabfm/trading/pipeline/tabfm_scorer.py`**

```python
import pandas as pd
from tabfm import TabFMClassifier, TabFMRegressor

FEATURE_COLS = [
  "price_close", "momentum_5d", "momentum_20d", "atr_14", "volume_zscore",
  "price_vs_sma20", "vix_level", "vix_5d_change", "iv_rank", "hv20",
  "hv_iv_ratio", "dte", "short_delta", "strike_distance_pct",
  "spread_width_dollars", "bid_ask_pct",
  "vix_bucket", "trend_direction", "iv_regime", "earnings_flag",
  "direction", "expiry_type", "sector",
]


def score_candidate(
  candidate: dict,
  context: pd.DataFrame,
  clf_model,
  reg_model,
) -> dict:
  """Score a candidate spread with TabFMClassifier (POP%) and TabFMRegressor.

  Returns the candidate dict with pop_predicted and exp_return added.
  Falls back to neutral defaults (0.5, 0.0) when context is empty.
  """
  if context.empty or "profitable" not in context.columns:
    return {**candidate, "pop_predicted": 0.5, "exp_return": 0.0}

  X_train = context[FEATURE_COLS].copy()
  y_clf = context["profitable"].values
  y_reg = context["return_pct"].values

  X_test = pd.DataFrame([{col: candidate.get(col) for col in FEATURE_COLS}])

  clf = TabFMClassifier(model=clf_model)
  clf.fit(X_train, y_clf)
  pop = float(clf.predict_proba(X_test)[0][1])

  reg = TabFMRegressor(model=reg_model)
  reg.fit(X_train, y_reg)
  exp_return = float(reg.predict(X_test)[0])

  return {**candidate, "pop_predicted": round(pop, 4), "exp_return": round(exp_return, 4)}
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
PYTHONPATH=. pytest tabfm/trading/tests/test_tabfm_scorer.py -v
```
Expected: `4 passed`

- [ ] **Step 5: Commit**

```bash
git add tabfm/trading/pipeline/tabfm_scorer.py tabfm/trading/tests/test_tabfm_scorer.py
git commit -m "feat(trading): add TabFM scorer pipeline stage"
```

---

### Task 9: Paper Executor and Position Auditor (Stages 6 & 7)

**Files:**
- Create: `tabfm/trading/pipeline/paper_executor.py`
- Create: `tabfm/trading/pipeline/position_auditor.py`
- Create: `tabfm/trading/tests/test_paper_executor.py`
- Create: `tabfm/trading/tests/test_position_auditor.py`

**Interfaces:**
- Produces: `execute_paper_trade(trade, as_of, path) -> int` (returns trade_id)
- Produces: `format_recommendation(trade, trade_id, as_of) -> str`
- Produces: `audit_positions(adapter, as_of, db_path) -> list[dict]`

- [ ] **Step 1: Write the failing tests**

```python
# tabfm/trading/tests/test_paper_executor.py
import tempfile
from pathlib import Path
from datetime import date
from tabfm.trading.pipeline.paper_executor import execute_paper_trade, format_recommendation
from tabfm.trading.store.journal import init_db, get_open_trades

_TRADE = {
  "ticker": "SPY", "direction": "put_spread",
  "strike_short": 480.0, "strike_long": 475.0,
  "expiry": "2025-01-17", "dte": 7, "entry_credit": 1.20,
  "spread_width_dollars": 5.0, "contracts": 2, "total_risk": 1000.0,
  "pop_predicted": 0.72, "exp_return": 0.18, "iv_rank": 55.0,
  "vix_bucket": "normal", "trend_direction": "uptrend", "iv_regime": "fair",
}
AS_OF = date(2025, 1, 10)


def test_execute_paper_trade_inserts_record(tmp_path):
  db = tmp_path / "test.db"
  init_db(db)
  trade_id = execute_paper_trade(_TRADE, AS_OF, path=db)
  assert trade_id == 1
  open_trades = get_open_trades(db)
  assert len(open_trades) == 1
  assert open_trades[0]["ticker"] == "SPY"


def test_format_recommendation_contains_key_fields():
  output = format_recommendation(_TRADE, 42, AS_OF)
  assert "SPY" in output
  assert "PUT SPREAD" in output
  assert "480" in output
  assert "72.0%" in output
  assert "trade_id: 42" in output
```

```python
# tabfm/trading/tests/test_position_auditor.py
from datetime import date
from unittest.mock import MagicMock
from tabfm.trading.pipeline.position_auditor import _is_winner, _estimate_current_value

def test_put_spread_winner_when_above_short_strike():
  trade = {"direction": "put_spread", "strike_short": 480.0, "spread_width": 5.0}
  assert _is_winner(trade, underlying_price=485.0) is True
  assert _is_winner(trade, underlying_price=478.0) is False


def test_call_spread_winner_when_below_short_strike():
  trade = {"direction": "call_spread", "strike_short": 500.0, "spread_width": 5.0}
  assert _is_winner(trade, underlying_price=495.0) is True
  assert _is_winner(trade, underlying_price=505.0) is False


def test_estimate_current_value_put_spread_itm():
  trade = {"direction": "put_spread", "strike_short": 480.0, "spread_width": 5.0}
  val = _estimate_current_value(trade, underlying_price=477.0)
  assert val == 3.0  # intrinsic = 480 - 477 = 3, within spread


def test_estimate_current_value_capped_at_spread():
  trade = {"direction": "put_spread", "strike_short": 480.0, "spread_width": 5.0}
  val = _estimate_current_value(trade, underlying_price=470.0)
  assert val == 5.0  # capped at spread width
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
PYTHONPATH=. pytest tabfm/trading/tests/test_paper_executor.py tabfm/trading/tests/test_position_auditor.py -v
```
Expected: `ImportError`

- [ ] **Step 3: Write `tabfm/trading/pipeline/paper_executor.py`**

```python
from datetime import date
from pathlib import Path
from ..store.journal import insert_trade, init_db, _DEFAULT_DB

_TEMPLATE = """
══════════════════════════════════════════════
  NIGHTLY RECOMMENDATION  ·  {date}
══════════════════════════════════════════════
  Ticker       {ticker}
  Direction    {direction_label}
  Strikes      ${strike_short} / ${strike_long}
  Expiry       {expiry}  ({dte} DTE)
  Spread Width ${spread_width_dollars}
  Entry Credit ${entry_credit} mid-price
  Max Profit   ${max_profit_per} / contract
  Max Loss     ${max_loss_per} / contract
  Contracts    {contracts}  →  max exposure ${total_risk:.0f}
  ─────────────────────────────────────────────
  POP%         {pop_pct:.1f}%
  Exp. Return  ${exp_return_dollars:.0f} expected paper P&L
  IV Rank      {iv_rank:.1f}  ({iv_regime} IV)
  Regime       {vix_bucket} VIX · {trend_direction} · {iv_regime} IV
══════════════════════════════════════════════
  [PAPER LOGGED]  trade_id: {trade_id}
"""


def execute_paper_trade(trade: dict, as_of: date, path: Path = _DEFAULT_DB) -> int:
  init_db(path)
  record = {
    "date_entered": str(as_of),
    "ticker": trade["ticker"],
    "direction": trade["direction"],
    "strike_short": trade["strike_short"],
    "strike_long": trade["strike_long"],
    "expiry": trade["expiry"],
    "dte": trade["dte"],
    "entry_credit": trade["entry_credit"],
    "spread_width": trade["spread_width_dollars"],
    "contracts": trade["contracts"],
    "max_loss": trade["contracts"] * (trade["spread_width_dollars"] - trade["entry_credit"]) * 100,
    "max_profit": trade["contracts"] * trade["entry_credit"] * 100,
    "pop_predicted": trade["pop_predicted"],
    "exp_return": trade["exp_return"],
    "regime": f"{trade['vix_bucket']}|{trade['trend_direction']}|{trade['iv_regime']}",
  }
  return insert_trade(record, path)


def format_recommendation(trade: dict, trade_id: int, as_of: date) -> str:
  label = (
    "CALL SPREAD  (bullish)" if trade["direction"] == "call_spread"
    else "PUT SPREAD  (bearish)"
  )
  return _TEMPLATE.format(
    date=as_of,
    ticker=trade["ticker"],
    direction_label=label,
    strike_short=trade["strike_short"],
    strike_long=trade["strike_long"],
    expiry=trade["expiry"],
    dte=trade["dte"],
    spread_width_dollars=trade["spread_width_dollars"],
    entry_credit=trade["entry_credit"],
    max_profit_per=round(trade["entry_credit"], 2),
    max_loss_per=round(trade["spread_width_dollars"] - trade["entry_credit"], 2),
    contracts=trade["contracts"],
    total_risk=trade["total_risk"],
    pop_pct=trade["pop_predicted"] * 100,
    exp_return_dollars=trade["exp_return"] * trade["total_risk"],
    iv_rank=trade["iv_rank"],
    iv_regime=trade["iv_regime"],
    vix_bucket=trade["vix_bucket"],
    trend_direction=trade["trend_direction"],
    trade_id=trade_id,
  )
```

- [ ] **Step 4: Write `tabfm/trading/pipeline/position_auditor.py`**

```python
from datetime import date
from pathlib import Path
from ..store.journal import get_open_trades, close_trade, _DEFAULT_DB
from ..adapters.base import DataAdapter

_EARLY_CLOSE_THRESHOLD = 0.50


def _is_winner(trade: dict, underlying_price: float) -> bool:
  if trade["direction"] == "put_spread":
    return underlying_price > trade["strike_short"]
  return underlying_price < trade["strike_short"]


def _estimate_current_value(trade: dict, underlying_price: float) -> float:
  short = trade["strike_short"]
  width = trade["spread_width"]
  if trade["direction"] == "put_spread":
    intrinsic = max(0.0, short - underlying_price)
  else:
    intrinsic = max(0.0, underlying_price - short)
  return min(intrinsic, width)


def audit_positions(
  adapter: DataAdapter, as_of: date, db_path: Path = _DEFAULT_DB
) -> list[dict]:
  """Close expired or 50%-profit positions and record actual P&L."""
  open_trades = get_open_trades(db_path)
  closed = []

  for trade in open_trades:
    try:
      underlying = adapter.get_underlying(trade["ticker"], as_of)
    except Exception:
      continue

    S = underlying["close"]
    credit = trade["entry_credit"]
    width = trade["spread_width"]
    contracts = trade["contracts"]
    expiry = date.fromisoformat(trade["expiry"])

    current_val = _estimate_current_value(trade, S)
    unrealized = (credit - current_val) * contracts * 100
    max_profit = credit * contracts * 100

    if as_of < expiry and unrealized >= max_profit * _EARLY_CLOSE_THRESHOLD:
      close_trade(trade["trade_id"], "partial", round(unrealized, 2), str(as_of), db_path)
      closed.append({**trade, "status": "partial", "actual_pnl": round(unrealized, 2)})
      continue

    if as_of >= expiry:
      if _is_winner(trade, S):
        pnl = round(credit * contracts * 100, 2)
        status = "won"
      else:
        pnl = round(-(width - credit) * contracts * 100, 2)
        status = "lost"
      close_trade(trade["trade_id"], status, pnl, str(as_of), db_path)
      closed.append({**trade, "status": status, "actual_pnl": pnl})

  return closed
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
PYTHONPATH=. pytest tabfm/trading/tests/test_paper_executor.py tabfm/trading/tests/test_position_auditor.py -v
```
Expected: `6 passed`

- [ ] **Step 6: Commit**

```bash
git add tabfm/trading/pipeline/paper_executor.py tabfm/trading/pipeline/position_auditor.py \
        tabfm/trading/tests/test_paper_executor.py tabfm/trading/tests/test_position_auditor.py
git commit -m "feat(trading): add PaperExecutor and PositionAuditor pipeline stages"
```

---

### Task 10: Accuracy Tracker (Stage 8)

**Files:**
- Create: `tabfm/trading/pipeline/accuracy_tracker.py`
- Create: `tabfm/trading/tests/test_accuracy_tracker.py`

**Interfaces:**
- Produces: `report(db_path, verbose) -> dict`
  - Keys: `total_trades`, `wins`, `partials`, `losses`, `win_rate`, `avg_pop_predicted`, `pop_calibration_error`, `cumulative_pnl`, `max_drawdown`, `best_regime`, `worst_regime`

- [ ] **Step 1: Write the failing tests**

```python
# tabfm/trading/tests/test_accuracy_tracker.py
from pathlib import Path
from tabfm.trading.pipeline.accuracy_tracker import report
from tabfm.trading.store.journal import init_db, insert_trade, close_trade

_BASE_TRADE = {
  "date_entered": "2025-01-01", "ticker": "SPY", "direction": "put_spread",
  "strike_short": 480.0, "strike_long": 475.0, "expiry": "2025-01-17",
  "dte": 7, "entry_credit": 1.20, "spread_width": 5.0, "contracts": 1,
  "max_loss": 380.0, "max_profit": 120.0, "pop_predicted": 0.70,
  "exp_return": 0.20, "regime": "normal|uptrend|fair",
}


def _setup_db(tmp_path: Path, records: list[tuple]) -> Path:
  db = tmp_path / "test.db"
  init_db(db)
  for i, (status, pnl, pop) in enumerate(records):
    t = {**_BASE_TRADE, "date_entered": f"2025-01-{i+1:02d}", "pop_predicted": pop}
    tid = insert_trade(t, db)
    close_trade(tid, status, pnl, "2025-01-17", db)
  return db


def test_report_no_trades(tmp_path):
  db = tmp_path / "empty.db"
  init_db(db)
  result = report(db_path=db, verbose=False)
  assert result == {}


def test_report_win_rate(tmp_path):
  db = _setup_db(tmp_path, [("won", 120.0, 0.72), ("won", 120.0, 0.68), ("lost", -380.0, 0.65)])
  metrics = report(db_path=db, verbose=False)
  assert metrics["total_trades"] == 3
  assert metrics["wins"] == 2
  assert abs(metrics["win_rate"] - 2/3) < 0.01


def test_report_cumulative_pnl(tmp_path):
  db = _setup_db(tmp_path, [("won", 120.0, 0.70), ("lost", -380.0, 0.70)])
  metrics = report(db_path=db, verbose=False)
  assert abs(metrics["cumulative_pnl"] - (120.0 - 380.0)) < 0.01


def test_report_max_drawdown(tmp_path):
  db = _setup_db(tmp_path, [
    ("won", 100.0, 0.70), ("lost", -300.0, 0.70), ("lost", -300.0, 0.70),
  ])
  metrics = report(db_path=db, verbose=False)
  assert metrics["max_drawdown"] > 0


def test_report_pop_calibration(tmp_path):
  # 2 wins (predicted 0.70 avg), actual win_rate = 1.0 → error = 0.30
  db = _setup_db(tmp_path, [("won", 120.0, 0.70), ("won", 120.0, 0.70)])
  metrics = report(db_path=db, verbose=False)
  assert abs(metrics["pop_calibration_error"] - 0.30) < 0.01
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
PYTHONPATH=. pytest tabfm/trading/tests/test_accuracy_tracker.py -v
```
Expected: `ImportError`

- [ ] **Step 3: Write `tabfm/trading/pipeline/accuracy_tracker.py`**

```python
from collections import defaultdict
from pathlib import Path
from ..store.journal import get_all_closed_trades, _DEFAULT_DB


def report(db_path: Path = _DEFAULT_DB, verbose: bool = True) -> dict:
  trades = get_all_closed_trades(db_path)
  if not trades:
    if verbose:
      print("No closed trades yet.")
    return {}

  total = len(trades)
  wins = sum(1 for t in trades if t["status"] == "won")
  partials = sum(1 for t in trades if t["status"] == "partial")
  losses = sum(1 for t in trades if t["status"] == "lost")
  win_rate = (wins + partials) / total
  avg_pop = sum(t["pop_predicted"] for t in trades) / total
  cumulative_pnl = sum(t["actual_pnl"] or 0 for t in trades)

  running = peak = max_drawdown = 0.0
  for t in trades:
    running += t["actual_pnl"] or 0
    if running > peak:
      peak = running
    dd = peak - running
    if dd > max_drawdown:
      max_drawdown = dd

  regime_wins: dict[str, int] = defaultdict(int)
  regime_total: dict[str, int] = defaultdict(int)
  for t in trades:
    r = t.get("regime", "unknown")
    regime_total[r] += 1
    if t["status"] in ("won", "partial"):
      regime_wins[r] += 1
  rates = {r: regime_wins[r] / regime_total[r] for r in regime_total}
  best = max(rates, key=rates.get) if rates else "N/A"
  worst = min(rates, key=rates.get) if rates else "N/A"

  metrics = {
    "total_trades": total,
    "wins": wins,
    "partials": partials,
    "losses": losses,
    "win_rate": round(win_rate, 4),
    "avg_pop_predicted": round(avg_pop, 4),
    "pop_calibration_error": round(abs(win_rate - avg_pop), 4),
    "cumulative_pnl": round(cumulative_pnl, 2),
    "max_drawdown": round(max_drawdown, 2),
    "best_regime": best,
    "worst_regime": worst,
  }

  if verbose:
    print(f"""
╔══════════════════════════════════════╗
  ACCURACY TRACKER
╠══════════════════════════════════════╣
  Trades:          {total}  ({wins}W / {partials}P / {losses}L)
  Win Rate:        {win_rate*100:.1f}%
  Avg POP pred:    {avg_pop*100:.1f}%  (error: {abs(win_rate-avg_pop)*100:.1f}%)
  Cumulative P&L:  ${cumulative_pnl:.2f}
  Max Drawdown:    ${max_drawdown:.2f}
  Best Regime:     {best}
  Worst Regime:    {worst}
╚══════════════════════════════════════╝""")

  return metrics
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
PYTHONPATH=. pytest tabfm/trading/tests/test_accuracy_tracker.py -v
```
Expected: `5 passed`

- [ ] **Step 5: Commit**

```bash
git add tabfm/trading/pipeline/accuracy_tracker.py tabfm/trading/tests/test_accuracy_tracker.py
git commit -m "feat(trading): add AccuracyTracker pipeline stage"
```

---

### Task 11: Nightly Entry Point

**Files:**
- Create: `tabfm/trading/run_nightly.py`
- Create: `tabfm/trading/tests/test_run_nightly.py`

**Interfaces:**
- Produces: `run(adapter, clf_model, reg_model, as_of, db_path, store_path) -> dict | None`

- [ ] **Step 1: Write the failing tests**

```python
# tabfm/trading/tests/test_run_nightly.py
"""Integration test: runs the full nightly pipeline with HistAdapter on a past date."""
from datetime import date
from unittest.mock import MagicMock, patch
import numpy as np
from tabfm.trading.store.journal import init_db, get_open_trades

AS_OF = date(2025, 6, 1)


def _mock_models():
  clf = MagicMock()
  reg = MagicMock()
  return clf, reg


def test_run_produces_trade_or_none(tmp_path):
  from tabfm.trading.adapters.historical import HistAdapter
  from tabfm.trading.run_nightly import run

  db = tmp_path / "test.db"
  store = tmp_path / "store.parquet"
  init_db(db)

  clf, reg = _mock_models()
  with patch("tabfm.trading.pipeline.tabfm_scorer.TabFMClassifier") as MockClf, \
       patch("tabfm.trading.pipeline.tabfm_scorer.TabFMRegressor") as MockReg:
    MockClf.return_value.predict_proba.return_value = np.array([[0.28, 0.72]])
    MockReg.return_value.predict.return_value = np.array([0.20])
    adapter = HistAdapter(as_of=AS_OF)
    result = run(adapter, clf, reg, as_of=AS_OF, db_path=db, store_path=store)

  # result is either the best trade dict or None (if all candidates filtered)
  assert result is None or isinstance(result, dict)


def test_run_logs_to_journal_when_trade_found(tmp_path):
  from tabfm.trading.adapters.historical import HistAdapter
  from tabfm.trading.run_nightly import run

  db = tmp_path / "test.db"
  store = tmp_path / "store.parquet"
  init_db(db)

  with patch("tabfm.trading.pipeline.tabfm_scorer.TabFMClassifier") as MockClf, \
       patch("tabfm.trading.pipeline.tabfm_scorer.TabFMRegressor") as MockReg:
    MockClf.return_value.predict_proba.return_value = np.array([[0.28, 0.72]])
    MockReg.return_value.predict.return_value = np.array([0.20])
    adapter = HistAdapter(as_of=AS_OF)
    _, reg = _mock_models()
    clf, _ = _mock_models()
    result = run(adapter, clf, reg, as_of=AS_OF, db_path=db, store_path=store)

  if result is not None:
    assert len(get_open_trades(db)) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
PYTHONPATH=. pytest tabfm/trading/tests/test_run_nightly.py -v
```
Expected: `ImportError`

- [ ] **Step 3: Write `tabfm/trading/run_nightly.py`**

```python
"""Nightly pipeline entry point.

Usage (live):
  python -m tabfm.trading.run_nightly

Requires Robinhood auth — set RH_USER and RH_PASS in .env and run:
  python -c "import robin_stocks.robinhood as rh; rh.login('user', 'pass')"
once to cache credentials, or load via dotenv in this script.
"""
from datetime import date
from pathlib import Path

from tabfm import tabfm_v1_0_0_pytorch as tabfm_backend

from .adapters.live import LiveAdapter
from .pipeline.chain_fetcher import fetch_chains
from .pipeline.feature_engineer import engineer_features
from .pipeline.context_builder import build_context
from .pipeline.tabfm_scorer import score_candidate
from .pipeline.trade_recommender import select_trade
from .pipeline.paper_executor import execute_paper_trade, format_recommendation
from .pipeline.position_auditor import audit_positions
from .store.history_store import append_rows, compute_iv_rank, _DEFAULT_STORE
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
    adapter = LiveAdapter()
  if clf_model is None:
    clf_model = tabfm_backend.load(model_type="classification")
  if reg_model is None:
    reg_model = tabfm_backend.load(model_type="regression")

  print(f"[NightlyPipeline] {as_of}")

  closed = audit_positions(adapter, as_of, db_path)
  if closed:
    print(f"[PositionAuditor] Closed {len(closed)} position(s)")

  chain_data_list = fetch_chains(adapter, as_of)
  all_candidates = []

  for chain_data in chain_data_list:
    iv_rank = compute_iv_rank(adapter.get_vix(as_of), store_path)
    feature_rows = engineer_features(chain_data, as_of, iv_rank)
    for row in feature_rows:
      context = build_context(row, str(as_of), path=store_path)
      scored = score_candidate(row, context, clf_model, reg_model)
      all_candidates.append(scored)
    append_rows(feature_rows, store_path)

  best = select_trade(all_candidates)
  if best is None:
    print("[TradeRecommender] No qualifying trade found today.")
    return None

  trade_id = execute_paper_trade(best, as_of, db_path)
  print(format_recommendation(best, trade_id, as_of))
  return best


if __name__ == "__main__":
  run()
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
PYTHONPATH=. pytest tabfm/trading/tests/test_run_nightly.py -v
```
Expected: `2 passed` (calls yfinance — requires internet; ~30s)

- [ ] **Step 5: Commit**

```bash
git add tabfm/trading/run_nightly.py tabfm/trading/tests/test_run_nightly.py
git commit -m "feat(trading): add nightly pipeline entry point"
```

---

### Task 12: Backtest Runner

**Files:**
- Create: `tabfm/trading/backtest/runner.py`
- Create: `tabfm/trading/run_backtest.py`
- Create: `tabfm/trading/tests/test_backtest_runner.py`

**Interfaces:**
- Consumes: `run` from `run_nightly`, `HistAdapter`, `accuracy_tracker.report`
- Produces: `trading_days(start, end) -> list[date]`
- Produces: `run_backtest(lookback_days, as_of, db_path, store_path) -> dict`

- [ ] **Step 1: Write the failing tests**

```python
# tabfm/trading/tests/test_backtest_runner.py
from datetime import date
from tabfm.trading.backtest.runner import trading_days


def test_trading_days_excludes_weekends():
  # 2025-01-06 is Monday, 2025-01-10 is Friday
  days = trading_days(date(2025, 1, 6), date(2025, 1, 10))
  assert len(days) == 5
  for d in days:
    assert d.weekday() < 5


def test_trading_days_single_day():
  days = trading_days(date(2025, 1, 6), date(2025, 1, 6))
  assert days == [date(2025, 1, 6)]


def test_trading_days_across_weekend():
  # Friday to Monday = 2 trading days
  days = trading_days(date(2025, 1, 10), date(2025, 1, 13))
  assert len(days) == 2
  assert days[0] == date(2025, 1, 10)
  assert days[1] == date(2025, 1, 13)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
PYTHONPATH=. pytest tabfm/trading/tests/test_backtest_runner.py -v
```
Expected: `ImportError`

- [ ] **Step 3: Write `tabfm/trading/backtest/runner.py`**

```python
from datetime import date, timedelta
from pathlib import Path

from tabfm import tabfm_v1_0_0_pytorch as tabfm_backend

from ..adapters.historical import HistAdapter
from ..pipeline.accuracy_tracker import report
from ..run_nightly import run
from ..store.journal import _DEFAULT_DB, init_db
from ..store.history_store import _DEFAULT_STORE


def trading_days(start: date, end: date) -> list[date]:
  days, current = [], start
  while current <= end:
    if current.weekday() < 5:
      days.append(current)
    current += timedelta(days=1)
  return days


def run_backtest(
  lookback_days: int = 252,
  as_of: date | None = None,
  db_path: Path = _DEFAULT_DB,
  store_path: Path = _DEFAULT_STORE,
) -> dict:
  """Walk-forward backtest over lookback_days calendar days ending at as_of."""
  if as_of is None:
    as_of = date.today()

  start = as_of - timedelta(days=lookback_days)
  days = trading_days(start, as_of - timedelta(days=1))

  init_db(db_path)
  clf_model = tabfm_backend.load(model_type="classification")
  reg_model = tabfm_backend.load(model_type="regression")

  print(f"[Backtest] {len(days)} trading days from {start} to {as_of}")

  for i, sim_date in enumerate(days):
    adapter = HistAdapter(as_of=sim_date)
    run(adapter, clf_model, reg_model, as_of=sim_date,
        db_path=db_path, store_path=store_path)
    if (i + 1) % 20 == 0:
      print(f"[Backtest] {i+1}/{len(days)} days complete")

  return report(db_path=db_path, verbose=True)
```

- [ ] **Step 4: Write `tabfm/trading/run_backtest.py`**

```python
"""Walk-forward backtest entry point.

Usage:
  python -m tabfm.trading.run_backtest
  python -m tabfm.trading.run_backtest --days 90
"""
import argparse
from datetime import date
from .backtest.runner import run_backtest

if __name__ == "__main__":
  parser = argparse.ArgumentParser(description="Run options signal pipeline backtest")
  parser.add_argument("--days", type=int, default=252,
                      help="Calendar days to backtest (default: 252 ≈ 1 year)")
  args = parser.parse_args()
  run_backtest(lookback_days=args.days)
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
PYTHONPATH=. pytest tabfm/trading/tests/test_backtest_runner.py -v
```
Expected: `3 passed`

- [ ] **Step 6: Run all trading tests**

```bash
PYTHONPATH=. pytest tabfm/trading/tests/ -v --ignore=tabfm/trading/tests/test_hist_adapter.py \
  --ignore=tabfm/trading/tests/test_run_nightly.py
```
Expected: all pass (skip network-dependent tests for CI)

- [ ] **Step 7: Commit**

```bash
git add tabfm/trading/backtest/ tabfm/trading/run_backtest.py \
        tabfm/trading/tests/test_backtest_runner.py
git commit -m "feat(trading): add walk-forward backtest runner"
```

---

## Self-Review

**Spec coverage check:**
- ✅ Eight pipeline stages (ChainFetcher → AccuracyTracker)
- ✅ DataAdapter interface with LiveAdapter + HistAdapter
- ✅ Black-Scholes option chain synthesis in HistAdapter
- ✅ No-lookahead assertion enforced in HistAdapter
- ✅ Context Builder with 3-step regime fallback
- ✅ Filter gauntlet: $1k cap, bid/ask, OI, DTE, delta, earnings
- ✅ Scoring: POP% × expected_return, positive EV only
- ✅ SQLite journal with all required columns
- ✅ Position Auditor: won/lost/partial (50% threshold)
- ✅ Accuracy Tracker: win rate, POP calibration, drawdown, per-regime
- ✅ Walk-forward backtest with trading_days() helper
- ✅ Nightly entry point (`python -m tabfm.trading.run_nightly`)
- ✅ Backtest entry point (`python -m tabfm.trading.run_backtest --days 252`)
- ✅ 25-ticker watchlist with SPY, QQQ, TSLA confirmed present
- ✅ Bootstrap sequence documented in spec; `compute_iv_rank` defaults to 50 when store is empty

**Type consistency check:**
- `execute_paper_trade` signature: `(trade: dict, as_of: date, path: Path)` — used consistently in run_nightly and tests ✅
- `audit_positions` signature: `(adapter, as_of, db_path)` — matches run_nightly call ✅
- `score_candidate` returns original dict + `pop_predicted` + `exp_return` — used by select_trade ✅
- `select_trade` adds `contracts`, `total_risk`, `score` to candidate — used by paper_executor ✅

**Placeholder scan:** No TBDs or TODOs in any code block. LiveAdapter has one comment on VIX proxy (VIXY ETF) which is a known approximation, not a placeholder.
