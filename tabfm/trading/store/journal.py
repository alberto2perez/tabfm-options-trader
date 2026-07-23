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
  pop_raw       REAL,
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
    # Migration for DBs created before the pop_raw column existed
    cols = {r[1] for r in conn.execute("PRAGMA table_info(paper_trades)")}
    if "pop_raw" not in cols:
      conn.execute("ALTER TABLE paper_trades ADD COLUMN pop_raw REAL")


def insert_trade(trade: dict, path: Path = _DEFAULT_DB) -> int:
  with sqlite3.connect(path) as conn:
    cur = conn.execute(
      """INSERT INTO paper_trades
         (date_entered, ticker, direction, strike_short, strike_long, expiry,
          dte, entry_credit, spread_width, contracts, max_loss, max_profit,
          pop_predicted, pop_raw, exp_return, regime)
         VALUES (:date_entered, :ticker, :direction, :strike_short, :strike_long,
                 :expiry, :dte, :entry_credit, :spread_width, :contracts,
                 :max_loss, :max_profit, :pop_predicted, :pop_raw, :exp_return, :regime)""",
      {**trade, "pop_raw": trade.get("pop_raw")},
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
