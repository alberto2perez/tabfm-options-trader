"""End-of-run portfolio summary: bankroll, open book, closed history, P&L."""
from datetime import date
from pathlib import Path

from .bankroll import get_bankroll
from ..store.journal import get_open_trades, get_all_closed_trades, _DEFAULT_DB


def portfolio_summary(db_path: Path = _DEFAULT_DB, as_of: date | None = None) -> str:
  opens = get_open_trades(db_path)
  closed = get_all_closed_trades(db_path)
  bk = get_bankroll(db_path)

  open_risk = sum(float(t["max_loss"] or 0) for t in opens)
  open_profit = sum(float(t["max_profit"] or 0) for t in opens)

  lines = []
  lines.append("╔══════════════════════════════════════════════════════╗")
  lines.append("  PORTFOLIO SUMMARY" + (f"  ·  {as_of}" if as_of else ""))
  lines.append("╠══════════════════════════════════════════════════════╣")

  mode = "RECOVERY (slice halved)" if bk.recovery_mode else "NORMAL"
  free = max(bk.exposure_limit - open_risk, 0.0)
  lines.append(f"  BANKROLL: equity ${bk.equity:,.2f}  (start ${bk.starting:,.0f} · realized ${bk.realized:,.2f})")
  lines.append(f"  Peak ${bk.peak_equity:,.2f} · drawdown {bk.drawdown_pct * 100:.1f}% · mode {mode}")
  lines.append(f"  Slice ${bk.slice_limit:,.2f} · exposure cap ${bk.exposure_limit:,.2f} (${open_risk:,.2f} open · ${free:,.2f} free)")
  lines.append("  ──────────────────────────────────────────────────────")

  lines.append(f"  OPEN POSITIONS ({len(opens)})")
  for t in opens:
    dte = ""
    if as_of is not None:
      try:
        dte = f"  ·  {(date.fromisoformat(str(t['expiry'])) - as_of).days}d left"
      except ValueError:
        pass
    lines.append(
      f"    #{t['trade_id']} {t['ticker']} {t['direction'].replace('_', ' ')} "
      f"{t['strike_short']:g}/{t['strike_long']:g} exp {t['expiry']} "
      f"x{t['contracts']}  risk ${t['max_loss']:.0f}{dte}"
    )
  if not opens:
    lines.append("    (none)")

  lines.append("  ──────────────────────────────────────────────────────")
  wins = sum(1 for t in closed if t["status"] in ("won", "partial"))
  losses = sum(1 for t in closed if t["status"] in ("lost", "stopped"))
  realized = sum(float(t["actual_pnl"] or 0) for t in closed)
  lines.append(f"  CLOSED: {len(closed)}  ({wins}W / {losses}L)"
               + (f"  ·  win rate {wins / len(closed) * 100:.0f}%" if closed else ""))
  lines.append(f"  Realized P&L:     ${realized:,.2f}")
  lines.append(f"  Open max risk:    ${open_risk:,.2f}")
  lines.append(f"  Open max profit:  ${open_profit:,.2f}")
  if closed:
    avg_pop = sum(float(t["pop_predicted"] or 0) for t in closed) / len(closed)
    lines.append(f"  Avg POP (closed): {avg_pop * 100:.1f}%  vs realized {wins / len(closed) * 100:.1f}%")
  lines.append("╚══════════════════════════════════════════════════════╝")
  return "\n".join(lines)
