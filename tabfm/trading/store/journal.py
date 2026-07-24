import sqlite3
from pathlib import Path

# Committed state: the data/ directory at repo root persists across cloud runs
_DEFAULT_DB = Path(__file__).parents[3] / "data" / "journal.db"

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
  entry_credit_mid REAL,
  spread_width  REAL NOT NULL,
  contracts     INTEGER NOT NULL,
  max_loss      REAL NOT NULL,
  max_profit    REAL NOT NULL,
  pop_predicted REAL NOT NULL,
  pop_raw       REAL,
  pop_market    REAL,
  mfe           REAL,
  mae           REAL,
  exp_return    REAL NOT NULL,
  regime        TEXT NOT NULL,
  strategy      TEXT NOT NULL DEFAULT 'model',
  status        TEXT NOT NULL DEFAULT 'open',
  actual_pnl    REAL,
  date_closed   TEXT
);
"""


def init_db(path: Path = _DEFAULT_DB) -> None:
  with sqlite3.connect(path) as conn:
    conn.execute(_SCHEMA)
    # Migration for DBs created before the pop_raw column existed
    cols = {r[1] for r in conn.execute("PRAGMA table_info(paper_trades)")}
    for col in ("pop_raw", "pop_market", "mfe", "mae", "entry_credit_mid"):
      if col not in cols:
        conn.execute(f"ALTER TABLE paper_trades ADD COLUMN {col} REAL")
    if "strategy" not in cols:
      conn.execute("ALTER TABLE paper_trades ADD COLUMN strategy TEXT")
    conn.execute("UPDATE paper_trades SET strategy='model' WHERE strategy IS NULL")


def insert_trade(trade: dict, path: Path = _DEFAULT_DB) -> int:
  with sqlite3.connect(path) as conn:
    cur = conn.execute(
      """INSERT INTO paper_trades
         (date_entered, ticker, direction, strike_short, strike_long, expiry,
          dte, entry_credit, entry_credit_mid, spread_width, contracts, max_loss, max_profit,
          pop_predicted, pop_raw, pop_market, exp_return, regime, strategy)
         VALUES (:date_entered, :ticker, :direction, :strike_short, :strike_long,
                 :expiry, :dte, :entry_credit, :entry_credit_mid, :spread_width, :contracts,
                 :max_loss, :max_profit, :pop_predicted, :pop_raw, :pop_market,
                 :exp_return, :regime, :strategy)""",
      {
        **trade,
        "pop_raw": trade.get("pop_raw"),
        "pop_market": trade.get("pop_market"),
        "entry_credit_mid": trade.get("entry_credit_mid"),
        "strategy": trade.get("strategy", "model"),
      },
    )
    return cur.lastrowid


def get_open_trades(path: Path = _DEFAULT_DB, strategy: str | None = "model") -> list[dict]:
  with sqlite3.connect(path) as conn:
    conn.row_factory = sqlite3.Row
    q = "SELECT * FROM paper_trades WHERE status = 'open'"
    params: tuple = ()
    if strategy is not None:
      q += " AND strategy = ?"
      params = (strategy,)
    return [dict(r) for r in conn.execute(q, params).fetchall()]


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


def get_all_closed_trades(path: Path = _DEFAULT_DB, strategy: str | None = "model") -> list[dict]:
  with sqlite3.connect(path) as conn:
    conn.row_factory = sqlite3.Row
    q = "SELECT * FROM paper_trades WHERE status != 'open'"
    params: tuple = ()
    if strategy is not None:
      q += " AND strategy = ?"
      params = (strategy,)
    return [dict(r) for r in conn.execute(q, params).fetchall()]


def update_excursions(
  trade_id: int, mfe: float, mae: float, path: Path = _DEFAULT_DB
) -> None:
  with sqlite3.connect(path) as conn:
    conn.execute(
      "UPDATE paper_trades SET mfe=?, mae=? WHERE trade_id=?",
      (mfe, mae, trade_id),
    )
