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
