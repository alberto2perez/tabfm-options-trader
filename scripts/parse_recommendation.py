"""Parse the newest card in RECOMMENDATIONS.md into a structured order ticket.

Read-only. Emits one JSON object on stdout so the /order-ticket skill can build
a ready-to-place ticket:

  {"status": "trade", ...fields...}                  a placeable credit-spread recommendation
  {"status": "gated", "date", "reason"}              newest night was gated (no entry)
  {"status": "trend_alert", "date", "reason"}        advisory about an open position
  {"status": "none"}                                 no recommendation card on record
  {"status": "error", "reason"}                      a card is present but couldn't be parsed

Usage: python scripts/parse_recommendation.py [--file PATH]
"""
import argparse
import json
import re
from pathlib import Path

_DEFAULT_RECS = Path(__file__).resolve().parent.parent / "data" / "RECOMMENDATIONS.md"

_REQUIRED = ("ticker", "direction", "strike_short", "strike_long", "expiry",
             "dte", "entry_credit", "entry_credit_mid", "contracts", "spread_width")


def _bullets(block: str) -> str | None:
  """Join all '- ' bullet lines in a block with '; ', or None if none found."""
  found = re.findall(r"^\s*-\s+(.*)$", block, re.MULTILINE)
  return "; ".join(b.strip() for b in found) if found else None


def newest_block(md_text: str) -> str | None:
  """The first '## ' section block (newest — cards are prepended)."""
  lines = md_text.splitlines()
  start = next((i for i, ln in enumerate(lines) if ln.startswith("## ")), None)
  if start is None:
    return None
  end = next((j for j in range(start + 1, len(lines)) if lines[j].startswith("## ")),
             len(lines))
  return "\n".join(lines[start:end])


def parse_card(block: str | None) -> dict:
  if block is None or not block.strip():
    return {"status": "none"}

  date_m = re.search(r"^##\s+(\S+)", block, re.MULTILINE)
  date = date_m.group(1) if date_m else None

  if re.search(r"GATED", block, re.IGNORECASE):
    return {"status": "gated", "date": date, "reason": _bullets(block)}

  if "TREND ALERT" in block:
    return {"status": "trend_alert", "date": date, "reason": _bullets(block)}

  if "NIGHTLY RECOMMENDATION" not in block:
    return {"status": "none"}

  def grab(pattern, cast=str):
    m = re.search(pattern, block, re.MULTILINE)
    return cast(m.group(1)) if m else None

  fields = {
    "date": date,
    "ticker": grab(r"^\s*Ticker\s+(\S+)"),
    "expiry": grab(r"Expiry\s+(\d{4}-\d{2}-\d{2})"),
    "dte": grab(r"Expiry\s+\d{4}-\d{2}-\d{2}\s+\((\d+)\s*DTE\)", int),
    "entry_credit": grab(r"Entry Credit\s+\$([\d.]+)", float),
    "entry_credit_mid": grab(
      r"Entry Credit\s+\$[\d.]+\s+est\. fill\s+\(mid\s+\$([\d.]+)\)", float),
    "contracts": grab(r"^\s*Contracts\s+(\d+)", int),
    "spread_width": grab(r"Spread Width\s+\$([\d.]+)", float),
  }

  dir_m = re.search(r"Direction\s+(CALL|PUT)\s+CREDIT", block)
  fields["direction"] = (
    {"CALL": "call_credit", "PUT": "put_credit"}[dir_m.group(1)] if dir_m else None)

  strikes_m = re.search(r"Strikes\s+\$([\d.]+)\s*/\s*\$([\d.]+)", block)
  fields["strike_short"] = float(strikes_m.group(1)) if strikes_m else None
  fields["strike_long"] = float(strikes_m.group(2)) if strikes_m else None

  missing = [k for k in _REQUIRED if fields.get(k) is None]
  if missing:
    return {"status": "error", "reason": f"could not parse: {', '.join(missing)}"}

  fields["option_type"] = "call" if fields["direction"] == "call_credit" else "put"
  fields["sell_strike"] = fields["strike_short"]
  fields["buy_strike"] = fields["strike_long"]
  fields["status"] = "trade"
  return fields


def main(argv=None) -> dict:
  ap = argparse.ArgumentParser(description="Parse newest RECOMMENDATIONS.md card to JSON")
  ap.add_argument("--file", default=str(_DEFAULT_RECS))
  args = ap.parse_args(argv)
  path = Path(args.file)
  result = parse_card(newest_block(path.read_text())) if path.exists() else {"status": "none"}
  print(json.dumps(result))
  return result


if __name__ == "__main__":
  main()
