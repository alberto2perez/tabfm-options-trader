from tabfm.trading.adapters.base import DataAdapter
from tabfm.trading.adapters.live import LiveAdapter


def test_live_adapter_is_data_adapter():
  assert issubclass(LiveAdapter, DataAdapter)


def test_live_adapter_instantiates():
  # LiveAdapter requires no constructor args (credentials handled by robin_stocks login)
  adapter = LiveAdapter()
  assert adapter is not None
