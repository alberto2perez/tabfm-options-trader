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
