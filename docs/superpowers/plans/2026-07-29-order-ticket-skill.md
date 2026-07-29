# Order Ticket skill Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `/order-ticket` skill that turns the newest `RECOMMENDATIONS.md` card into a ready-to-place Robinhood order ticket (exact SELL/BUY legs, expiry, limit credit, contracts) plus a live read-only fill-fidelity PASS/SKIP verdict.

**Architecture:** A deterministic Python parser (`scripts/parse_recommendation.py`) extracts the newest card into structured JSON (or a gated/none/error status). A thin orchestration skill (`.claude/skills/order-ticket/SKILL.md`) runs it, resolves each leg's `option_id` and live quotes via read-only Robinhood MCP, computes the fill-fidelity verdict, and prints the ticket. Read-only throughout — it builds the ticket you place by hand; it never places or reviews an order.

**Tech Stack:** Python 3 (stdlib only: `argparse`, `json`, `re`, `pathlib`), pytest via subprocess for the parser, Claude Code skill markdown with `allowed-tools`.

## Global Constraints

- **Read-only advisory:** parser and skill MUST NOT place/cancel/review orders or mutate any file. The skill's `allowed-tools` includes NO order-placing/reviewing tools.
- **Robinhood MCP is quote-only here:** use only `get_option_chains`, `get_option_instruments`, `get_option_quotes` (already allow-listed, read-only). Do NOT use `review_option_order` — it requires an agent-allowed account the options account (`547182618`, `agentic_allowed=false`) isn't.
- **Never build a ticket from a bad parse:** a malformed card yields `{"status":"error"}`, not a half-filled ticket.
- **Gated newest card stops the flow:** do not resurrect stale prior recommendations.
- **Fill-fidelity threshold:** `TOL = 0.05`. PASS if `live_net_credit ≥ entry_credit − TOL`; SKIP if below; UNRELIABLE if a leg's bid-ask spread > 30% of its mid or a quote is missing.
- **Direction → legs:** the card lists the short (sold) strike first, long (bought) second. `call_credit` → both legs `call`; `put_credit` → both legs `put`. `sell_strike = strike_short`, `buy_strike = strike_long` in both.
- **Card label format (verbatim from `paper_executor.py` `_TEMPLATE`):** `Ticker <t>` / `Direction <CALL|PUT> CREDIT SPREAD …` / `Strikes $<short> / $<long>` / `Expiry <YYYY-MM-DD>  (<dte> DTE)` / `Spread Width $<w>` / `Entry Credit $<credit> est. fill (mid $<mid>)` / `Contracts <n>  → …`.

---

## File Structure

- `scripts/parse_recommendation.py` (new) — deterministic card parser; prints one JSON object.
- `tabfm/trading/tests/test_parse_recommendation.py` (new) — pytest exercising the parser via subprocess with tmp fixtures.
- `.claude/skills/order-ticket/SKILL.md` (new) — orchestration skill.

---

### Task 1: Recommendation parser + tests

**Files:**
- Create: `scripts/parse_recommendation.py`
- Test: `tabfm/trading/tests/test_parse_recommendation.py`

**Interfaces:**
- Consumes: `data/RECOMMENDATIONS.md` format (card labels above).
- Produces: an executable `scripts/parse_recommendation.py` that, run as
  `python scripts/parse_recommendation.py [--file PATH]`, prints exactly one
  JSON object to stdout and exits 0. Shapes the skill (Task 2) depends on:
  - `{"status":"trade","date","ticker","direction","strike_short","strike_long","expiry","dte","entry_credit","entry_credit_mid","contracts","spread_width","option_type","sell_strike","buy_strike"}`
    where `direction` ∈ `{"call_credit","put_credit"}`, `option_type` ∈ `{"call","put"}`, strikes/credits/width are floats, `dte`/`contracts` ints.
  - `{"status":"gated","date","reason"}`
  - `{"status":"none"}`
  - `{"status":"error","reason"}`

- [ ] **Step 1: Write the failing test**

Create `tabfm/trading/tests/test_parse_recommendation.py`:

```python
import json
import pathlib
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parents[3]
SCRIPT = REPO / "scripts" / "parse_recommendation.py"

_CALL_CARD = """# Nightly Recommendations

## 2026-07-24

```
==============================================
  NIGHTLY RECOMMENDATION  ·  2026-07-24
==============================================
  Ticker       SPY
  Direction    CALL CREDIT SPREAD  (bearish/neutral)
  Strikes      $750.0 / $755.0
  Expiry       2026-08-21  (28 DTE)
  Spread Width $5.0
  Entry Credit $2.15 est. fill (mid $2.19)
  Max Profit   $2.15 / contract
  Max Loss     $2.85 / contract
  Contracts    1  ->  max exposure $285
==============================================
  [PAPER LOGGED]  trade_id: 2
```
"""

_PUT_CARD = _CALL_CARD.replace(
    "CALL CREDIT SPREAD  (bearish/neutral)", "PUT CREDIT SPREAD  (bullish/neutral)"
).replace("$750.0 / $755.0", "$620.0 / $615.0")

_GATED_CARD = """# Nightly Recommendations

## 2026-07-28

GATED — no new entries.
- FOMC rate decision next session
"""

_EMPTY = "# Nightly Recommendations\n"

_MALFORMED = _CALL_CARD.replace("  Contracts    1  ->  max exposure $285\n", "")


def _run(md: str, tmp_path) -> dict:
    f = tmp_path / "RECOMMENDATIONS.md"
    f.write_text(md)
    r = subprocess.run(
        [sys.executable, str(SCRIPT), "--file", str(f)],
        capture_output=True, text=True, cwd=str(REPO),
    )
    assert r.returncode == 0, f"nonzero exit: {r.stderr}"
    return json.loads(r.stdout)


def test_call_credit_card(tmp_path):
    d = _run(_CALL_CARD, tmp_path)
    assert d["status"] == "trade"
    assert d["ticker"] == "SPY"
    assert d["direction"] == "call_credit"
    assert d["option_type"] == "call"
    assert d["strike_short"] == 750.0 and d["strike_long"] == 755.0
    assert d["sell_strike"] == 750.0 and d["buy_strike"] == 755.0
    assert d["expiry"] == "2026-08-21" and d["dte"] == 28
    assert d["entry_credit"] == 2.15 and d["entry_credit_mid"] == 2.19
    assert d["contracts"] == 1 and d["spread_width"] == 5.0


def test_put_credit_card(tmp_path):
    d = _run(_PUT_CARD, tmp_path)
    assert d["status"] == "trade"
    assert d["direction"] == "put_credit"
    assert d["option_type"] == "put"
    # short (sold) is the HIGHER strike for a put credit spread
    assert d["sell_strike"] == 620.0 and d["buy_strike"] == 615.0


def test_gated_card(tmp_path):
    d = _run(_GATED_CARD, tmp_path)
    assert d["status"] == "gated"
    assert d["date"] == "2026-07-28"
    assert "FOMC" in (d["reason"] or "")


def test_empty_file(tmp_path):
    assert _run(_EMPTY, tmp_path) == {"status": "none"}


def test_malformed_card(tmp_path):
    d = _run(_MALFORMED, tmp_path)
    assert d["status"] == "error"
    assert "contracts" in d["reason"].lower()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `venv/bin/python -m pytest tabfm/trading/tests/test_parse_recommendation.py -v`
Expected: FAIL — `scripts/parse_recommendation.py` does not exist yet.

- [ ] **Step 3: Write the parser**

Create `scripts/parse_recommendation.py`:

```python
"""Parse the newest card in RECOMMENDATIONS.md into a structured order ticket.

Read-only. Emits one JSON object on stdout so the /order-ticket skill can build
a ready-to-place ticket:

  {"status": "trade", ...fields...}      a placeable credit-spread recommendation
  {"status": "gated", "date", "reason"}  newest night was gated (no entry)
  {"status": "none"}                     no recommendation card on record
  {"status": "error", "reason"}          a card is present but couldn't be parsed

Usage: python scripts/parse_recommendation.py [--file PATH]
"""
import argparse
import json
import re
from pathlib import Path

_DEFAULT_RECS = Path(__file__).resolve().parent.parent / "data" / "RECOMMENDATIONS.md"

_REQUIRED = ("ticker", "direction", "strike_short", "strike_long", "expiry",
             "dte", "entry_credit", "entry_credit_mid", "contracts", "spread_width")


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

  if re.search(r"GATED|no new entries", block, re.IGNORECASE):
    reason_m = re.search(r"^\s*-\s+(.*)$", block, re.MULTILINE)
    return {"status": "gated", "date": date,
            "reason": reason_m.group(1).strip() if reason_m else None}

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
```

- [ ] **Step 4: Make it executable**

Run: `chmod +x scripts/parse_recommendation.py`

- [ ] **Step 5: Run the test to verify it passes**

Run: `venv/bin/python -m pytest tabfm/trading/tests/test_parse_recommendation.py -v`
Expected: PASS (5 tests).

- [ ] **Step 6: Eyeball against the live file**

Run: `python scripts/parse_recommendation.py`
Expected: with the current repo state (newest card is the 2026-07-28 GATED
entry), prints `{"status": "gated", "date": "2026-07-28", "reason": "FOMC rate decision next session"}`.

- [ ] **Step 7: Commit**

```bash
git add scripts/parse_recommendation.py tabfm/trading/tests/test_parse_recommendation.py
git commit -m "feat: parse_recommendation.py — newest RECOMMENDATIONS.md card to JSON"
```

---

### Task 2: /order-ticket orchestration skill

**Files:**
- Create: `.claude/skills/order-ticket/SKILL.md`

**Interfaces:**
- Consumes: `scripts/parse_recommendation.py` JSON (Task 1); read-only MCP tools
  `get_option_chains`, `get_option_instruments`, `get_option_quotes`.
- Produces: a skill invocable as `/order-ticket` that prints an order ticket +
  fill-fidelity verdict. No code consumers.

- [ ] **Step 1: Write the skill file**

Create `.claude/skills/order-ticket/SKILL.md`:

```markdown
---
name: order-ticket
description: Turn the latest advisor recommendation into a ready-to-place Robinhood order ticket (exact SELL/BUY legs, expiry, limit credit, contracts) plus a live read-only fill-fidelity PASS/SKIP check. Use when the user says to execute/place the recommendation or asks for an order ticket.
allowed-tools: Bash(python scripts/parse_recommendation.py), Bash(python3 scripts/parse_recommendation.py), mcp__robinhood-trading__get_option_chains, mcp__robinhood-trading__get_option_instruments, mcp__robinhood-trading__get_option_quotes
---

Build ONE ready-to-place order ticket from the latest recommendation, with a
live fill-fidelity check. This is READ-ONLY and advisory: NEVER place, cancel,
or review an order; NEVER mutate any file. The Robinhood MCP cannot place
multi-leg spreads anyway — the operator places the ticket by hand in the app.

## Steps

1. **Parse the latest recommendation.** Run
   `python scripts/parse_recommendation.py` (fall back to `python3` if needed).
   It prints one JSON object:
   - `{"status":"gated",...}` → print `Latest night was GATED (<reason>) —
     nothing to place.` and STOP.
   - `{"status":"none"}` → print `No recommendation on record — nothing to
     place.` and STOP.
   - `{"status":"error","reason":...}` → print `Could not parse the latest card
     (<reason>) — not building a ticket.` and STOP.
   - `{"status":"trade",...}` → continue. Fields: `ticker`, `direction`
     (call_credit/put_credit), `option_type` (call/put), `sell_strike`,
     `buy_strike`, `expiry`, `dte`, `entry_credit`, `entry_credit_mid`,
     `contracts`, `spread_width`.

2. **Sanity-check the expiry.** If `expiry` is today or in the past, warn
   prominently (`⚠️ expiry <date> is stale/expired`) — a days-old recommendation
   after gated nights is not placeable as-is.

3. **Resolve the two option_ids (read-only).** `get_option_chains` for `ticker`
   → `get_option_instruments` filtered by `expiration_date=expiry`,
   `strike_price`, and `type=option_type`, once for `sell_strike` and once for
   `buy_strike`. If either leg fails to resolve, report which one and STOP (no
   partial ticket).

4. **Fill-fidelity check (read-only).** `get_option_quotes` for both option_ids.
   For each leg compute `mid = (bid + ask) / 2` (or `mark`). Then:
   - `live_net_credit = live_sell_mid − live_buy_mid`.
   - If either leg's `(ask − bid)` > 0.30 × its mid, or a quote is missing →
     verdict `⚠️ UNRELIABLE (quotes too thin/absent)`.
   - Else if `live_net_credit ≥ entry_credit − 0.05` → `✅ PASS`.
   - Else → `⚠️ SKIP — live credit $<entry_credit − live_net_credit> below
     recommendation; don't chase.`
   - If ALL MCP calls are unavailable (offline/after-hours) → skip this block,
     print `— live quotes unavailable (offline/after-hours)`, and still print
     the ticket (the SELL/BUY lines stand).

5. **Print the ticket.** Compute `max_loss = (spread_width − entry_credit) *
   100 * contracts` and `max_profit = entry_credit * 100 * contracts`. Use this
   exact shape:

    ═══════════════════════════════════════════════
      ORDER TICKET  ·  <TICKER> <DIRECTION>  ·  <date> rec
    ═══════════════════════════════════════════════
      Place in the Robinhood app (multi-leg not available via API):

        SELL  <contracts>  <ticker>  $<sell_strike> <CALL|PUT>  exp <expiry>   ← short leg
        BUY   <contracts>  <ticker>  $<buy_strike>  <CALL|PUT>  exp <expiry>   ← long leg
        Order type   LIMIT  ·  net CREDIT  $<entry_credit>  (or better)
        Spread width $<spread_width>  ·  max loss $<max_loss>  ·  max profit $<max_profit>
      ─────────────────────────────────────────────
      FILL FIDELITY (live quotes)
        Recommended   $<entry_credit>  (card mid $<entry_credit_mid>)
        Live short    $<mid> mid   (<bid> / <ask>)
        Live long     $<mid> mid   (<bid> / <ask>)
        Live net      $<live_net_credit>  →  <verdict>
      ─────────────────────────────────────────────
      option_ids (for reference)
        short  <id>   long  <id>
    ═══════════════════════════════════════════════

## Rules

- A `⚠️ SKIP` verdict still prints the full ticket, but leads with the SKIP line.
- Never emit a ticket for a `gated`/`none`/`error` parse — those STOP at step 1.
- `option_type` from the JSON decides CALL vs PUT for BOTH legs; `sell_strike`
  is always the short leg, `buy_strike` the long leg (the parser already mapped
  direction → legs; do not re-derive).
- If live quotes are unavailable, the SELL/BUY lines are still correct — the
  operator can place at the recommended limit; only the verdict is withheld.
```

- [ ] **Step 2: Static validation**

Confirm the file exists and the frontmatter is well-formed:
Run: `ls .claude/skills/order-ticket/SKILL.md`
Check by reading it: `name: order-ticket`; `allowed-tools` contains ONLY the
parser Bash command(s) and the three read-only MCP quote tools — NO
`place_option_order`, `review_option_order`, `cancel_*`, `Write`, or `Edit`.

- [ ] **Step 3: Commit**

```bash
git add .claude/skills/order-ticket/SKILL.md
git commit -m "feat: /order-ticket skill — recommendation to placeable ticket + fill check"
```

- [ ] **Step 4: Live eyeball (controller, read-only)**

Invoke `/order-ticket`. With the current repo state the newest card is the
2026-07-28 GATED entry, so it should print `Latest night was GATED (FOMC rate
decision next session) — nothing to place.` and stop — exercising the gated
path end-to-end. Confirm no orders placed and `git status` clean afterward.

---

## Self-Review

**Spec coverage:**
- Parser: newest card → JSON, gated/none/error/trade statuses, direction→legs → Task 1. ✓
- Skill: parse → resolve legs → live fill check → ticket → Task 2 steps 1–5. ✓
- Read-only, no place/review tools → Global Constraints + Task 2 step 2 static check. ✓
- Fill-fidelity verdict (PASS/SKIP/UNRELIABLE, TOL 0.05, 30% wide guard) → Task 2 step 4. ✓
- Output ticket format + max_loss/max_profit formulas → Task 2 step 5. ✓
- Edge cases (gated stop, none, error, MCP-unavailable, illiquid, stale expiry, unresolved leg) → Task 2 steps 1–4 + Rules. ✓
- Testing (parser via subprocess fixtures; skill eyeballed) → Task 1 steps 1–6, Task 2 step 4. ✓

**Placeholder scan:** none — full parser, tests, and skill content inline.

**Type consistency:** JSON keys emitted by the parser in Task 1 (`status`,
`direction`, `option_type`, `sell_strike`, `buy_strike`, `entry_credit`,
`entry_credit_mid`, `spread_width`, `contracts`, `dte`, `expiry`, `ticker`,
`strike_short`, `strike_long`, `date`, `reason`) match exactly the keys the
Task 2 skill reads. Verdict thresholds (`TOL 0.05`, `30%`) identical to the
Global Constraints and the spec. ✓
