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
  losses = sum(1 for t in trades if t["status"] in ("lost", "stopped"))
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

  benchmarked = [t for t in trades if t.get("pop_market") is not None]
  if benchmarked:
    def _brier(key: str) -> float:
      return sum(
        (float(t[key]) - (1 if t["status"] in ("won", "partial") else 0)) ** 2
        for t in benchmarked
      ) / len(benchmarked)
    metrics["brier_tabfm"] = round(_brier("pop_predicted"), 4)
    metrics["brier_market"] = round(_brier("pop_market"), 4)
    metrics["brier_n"] = len(benchmarked)

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

  if "brier_tabfm" in metrics and verbose:
    print(f"  Brier (lower=better): TabFM {metrics['brier_tabfm']:.4f} vs "
          f"market {metrics['brier_market']:.4f}  (n={metrics['brier_n']})")

  return metrics
