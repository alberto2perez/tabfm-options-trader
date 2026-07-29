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
