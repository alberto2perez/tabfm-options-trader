# Order Ticket skill — design

**Date:** 2026-07-29
**Status:** Approved design, ready for implementation plan

## Problem

The advisor recommends a credit spread; the operator must then place it in
Robinhood. The Robinhood MCP **cannot** place it (single-leg only —
multi-leg/spread/combo orders are unsupported via the API), and neither
account is both `agentic_allowed=true` and options-enabled. So placement
stays manual, in the Robinhood app. Today the operator hand-translates the
recommendation card into an order (which strike to sell vs buy, expiry, limit
credit) and separately eyeballs whether live prices still support the modeled
credit. This skill collapses that into one read-only invocation: a
ready-to-place order ticket plus a live fill-fidelity go/no-go.

## Goal

`/order-ticket` produces, read-only:

- the exact spread to enter in the RH app — SELL leg, BUY leg, expiry, limit
  net credit, contracts, defined risk; and
- a **fill-fidelity verdict** from live quotes — can you still collect ~the
  recommended credit right now (PASS), or has it decayed (SKIP)?

It never places or reviews an order. It builds the ticket you place by hand.

## Non-goals

- No order placement, cancellation, or `review_option_order` (the latter also
  requires an agent-allowed account the options account isn't).
- No journal or `RECOMMENDATIONS.md` mutation.
- No resurrecting stale recommendations: a GATED newest card stops the flow.

## Approach

Approach A — a **testable parser helper + thin orchestration skill**, mirroring
the `latest-run-results` architecture. The brittle, real-money-critical part
(card parsing) is deterministic Python with unit tests; the MCP quote/verdict
layer is skill prose. Rejected: all-in-skill prose parsing (untestable, risky
for a real-money ticket) and folding into `latest-run-results` (different
concern — review vs act).

### Components

1. **`scripts/parse_recommendation.py`** (new) — deterministic parser. Reads the
   newest `## ` block from `data/RECOMMENDATIONS.md` (newest is prepended, so
   the first block) and prints one JSON object to stdout:
   - GATED / no-entry card (body contains `GATED` or `no new entries`):
     `{"status": "gated", "date": "<date>", "reason": "<first bullet, if any>"}`
   - No card / empty file: `{"status": "none"}`
   - Malformed trade card (a required field missing): `{"status": "error",
     "reason": "<what was missing>"}`
   - Valid trade card: `{"status": "trade", "date", "ticker", "direction",
     "strike_short", "strike_long", "expiry", "dte", "entry_credit",
     "entry_credit_mid", "contracts", "spread_width", "sell_strike",
     "buy_strike", "option_type"}`

   Parsed from the card's fixed labels (see `paper_executor.py` card template):
   - `Ticker SPY` → `ticker`
   - `Direction CALL CREDIT SPREAD` → `direction` (`call_credit` / `put_credit`)
   - `Strikes $750.0 / $755.0` → `strike_short` (first), `strike_long` (second)
   - `Expiry 2026-08-21  (28 DTE)` → `expiry`, `dte`
   - `Entry Credit $2.15 est. fill (mid $2.19)` → `entry_credit`,
     `entry_credit_mid`
   - `Contracts 1` → `contracts`
   - `Spread Width $5.0` → `spread_width`

   **Direction → legs mapping** (emitted so the skill never re-derives it; the
   card lists short first, long second, so short=sell, long=buy):
   - `call_credit` (bearish): `option_type="call"`, `sell_strike=strike_short`
     (lower), `buy_strike=strike_long` (higher).
   - `put_credit` (bullish): `option_type="put"`, `sell_strike=strike_short`
     (higher), `buy_strike=strike_long` (lower).

2. **`.claude/skills/order-ticket/SKILL.md`** (new) — orchestration, invoked
   `/order-ticket`. `allowed-tools` scoped to
   `Bash(python … parse_recommendation.py)`, `mcp__robinhood-trading__get_option_chains`,
   `mcp__robinhood-trading__get_option_instruments`,
   `mcp__robinhood-trading__get_option_quotes` (all read-only, already
   allow-listed). No order-placing/reviewing tools.

### Flow when invoked

1. Run the parser. `gated` → print the one-line gated message and stop;
   `none`/`error` → print the status and stop.
2. From the parsed legs, resolve each `option_id`: `get_option_chains` (ticker)
   → `get_option_instruments` filtered by `expiration_date`, `strike_price`,
   `type`.
3. `get_option_quotes` for both legs → live mids → `live_net_credit` and the
   verdict.
4. Print the order ticket + fill-fidelity block.

### Fill-fidelity verdict

Per leg: mid = `(bid+ask)/2` (or `mark`).
`live_net_credit = live_short_mid − live_long_mid`.

Against the recommended `entry_credit` (the friction-adjusted fill the model
assumed), with `TOL = $0.05` (stated constant, tunable):

- **✅ PASS** if `live_net_credit ≥ entry_credit − TOL` — a limit at the
  recommended credit should fill; place it.
- **⚠️ SKIP** if `live_net_credit < entry_credit − TOL` — credit decayed below
  the recommendation; don't chase (OPERATING.md). Show the shortfall.
- **⚠️ UNRELIABLE** if either leg's bid-ask spread > 30% of its mid, or a quote
  is missing — quotes too thin to trust; no false PASS/SKIP.

The block prints all three inputs (recommended `entry_credit` + card mid, each
live leg mid with bid/ask, and `live_net_credit`) so the verdict is transparent.

### Output ticket format

```
═══════════════════════════════════════════════
  ORDER TICKET  ·  <TICKER DIRECTION>  ·  <card date> rec
═══════════════════════════════════════════════
  Place in the Robinhood app (multi-leg not available via API):

    SELL  <n>  <ticker>  $<sell_strike> <CALL|PUT>  exp <expiry>   ← short leg
    BUY   <n>  <ticker>  $<buy_strike>  <CALL|PUT>  exp <expiry>   ← long leg
    Order type   LIMIT  ·  net CREDIT  $<entry_credit>  (or better)
    Spread width $<w>  ·  max loss $<ml>  ·  max profit $<mp>
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
```

- `max loss` = `(spread_width − entry_credit) × 100 × contracts`;
  `max profit` = `entry_credit × 100 × contracts`.
- SKIP leads with `⚠️ SKIP — live credit $X below recommendation; don't chase.`
  and still prints the full ticket (informational).
- `gated`/`none`/`error` print a one-line status, no ticket (e.g. `Latest night
  was GATED (<reason>) — nothing to place.`).

### Edge cases

- **Gated newest card** → gated message, stop.
- **No card / empty file** → "no recommendation on record."
- **Malformed card** → error status, refuse to build a ticket.
- **MCP unavailable / offline / after-hours** → print the ticket from the parsed
  card; FILL FIDELITY shows `— live quotes unavailable`. The SELL/BUY lines
  still stand (place at the recommended limit).
- **Illiquid / wide or missing quote** → `⚠️ UNRELIABLE`, no false PASS.
- **Stale / expired expiry** (card expiry at/near today or past) → flag; do not
  silently ticket a near-dead contract.
- **Chain/instrument not found** → report which leg failed to resolve; no
  partial ticket.

## Testing

- `parse_recommendation.py` gets pytest coverage — the deterministic,
  real-money-critical part, with inline fixtures (no dependence on the live
  `RECOMMENDATIONS.md`):
  - a `call_credit` trade card → correct fields + `sell_strike`/`buy_strike`/
    `option_type` mapping;
  - a `put_credit` trade card → sell = higher strike, buy = lower, type `put`;
  - a GATED card → `{"status": "gated"}` with reason;
  - an empty / no-`##` file → `{"status": "none"}`;
  - a card missing a required field → `{"status": "error"}`.
- The MCP/quote/verdict layer is skill prose, validated by invoking
  `/order-ticket` once and eyeballing (read-only; places nothing).

## Files

- `scripts/parse_recommendation.py` (new)
- `tabfm/trading/tests/test_parse_recommendation.py` (new)
- `.claude/skills/order-ticket/SKILL.md` (new)
