"""Per-session market state (VIX, SPY median IV) persisted as CSV.

Lives next to the journal (data/market_history.csv, committed) so cloud runs
inherit it from the repo clone and push updates back.
"""
import csv
from datetime import date
from pathlib import Path

_FILENAME = "market_history.csv"


def record_market_day(
  data_dir: Path, as_of: date, vix: float, median_iv: float | None
) -> None:
  path = Path(data_dir) / _FILENAME
  rows = {r["date"]: r for r in load_market_history(data_dir, n=10_000)}
  rows[str(as_of)] = {"date": str(as_of), "vix": vix, "median_iv": median_iv}
  with open(path, "w", newline="") as f:
    writer = csv.writer(f)
    writer.writerow(["date", "vix", "median_iv"])
    for key in sorted(rows):
      r = rows[key]
      writer.writerow([r["date"], r["vix"], "" if r["median_iv"] is None else r["median_iv"]])


def load_market_history(data_dir: Path, n: int = 10) -> list[dict]:
  path = Path(data_dir) / _FILENAME
  if not path.exists():
    return []
  with open(path, newline="") as f:
    rows = [
      {
        "date": r["date"],
        "vix": float(r["vix"]),
        "median_iv": float(r["median_iv"]) if r["median_iv"] else None,
      }
      for r in csv.DictReader(f)
    ]
  rows.sort(key=lambda r: r["date"])
  return rows[-n:]
