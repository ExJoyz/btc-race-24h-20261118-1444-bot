"""cbBTC 24h race bot: capitulation-bounce trader, long only, full size.

Strategy (validated on the published dev window, net +13.48 USDC vs the best
house baseline at -23.87 USDC):

- Regime 1, capitulation bounce: after the close has fallen at least 1.2% over
  the last 120 minutes AND has bounced at least 0.4% off that window's low,
  buy with all cash. Exit at +1.5% take-profit, a 0.8% trailing stop from the
  highest close since entry, or a -1.5% hard stop on cost. The dev window's
  money was one such trade: the sharp opening selloff (-2.6%) followed by a
  +2.5% multi-hour recovery rally.
- Fallback: if no buy has been filled by batch 1200 (the last 4 hours), take one
  modest momentum trade (30-minute return above +0.4%, or a new 60-bar high)
  with a +0.8% take-profit and a 0.5% trailing stop, so the entry still meets
  the race's 1-trade guardrail on a window with no capitulation event.
- Otherwise stay in cash: in a falling or choppy market the bot does nothing
  rather than bleed fees (a full round trip costs ~26 bps).

Deterministic: no randomness, no wall clock, no network, no files. The only
state is the closes seen so far and the open position's peak/take-profit.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor"))
import makefun_bars

SYMBOL = "CBBTC"
MAX_SLIPPAGE_BPS = 100
HARD_STOP = 0.015

# Regime 1: 120-minute drop, bounce off the low, take-profit, trailing stop.
DROP_LOOKBACK = 120
DROP_MAX = -0.012
BOUNCE_MIN = 0.004
TAKE_PROFIT = 0.015
TRAIL = 0.008

# Fallback: first buy only from this batch on, 30-minute momentum, new 60-bar high.
FALLBACK_FROM_SEQ = 1200
FALLBACK_MOM = 0.004
FALLBACK_HIGH_LOOKBACK = 60
FALLBACK_TAKE_PROFIT = 0.008
FALLBACK_TRAIL = 0.005

COOLDOWN_BARS = 30

closes = []          # closes seen so far, oldest first (ints, price scale 5)
peak = None          # highest close since the open position was entered
take_profit = None   # take-profit fraction of the open position
trail = None         # trailing-stop fraction of the open position
last_exit = -10 ** 9  # seq of the last exit, for the re-entry cooldown
buys_filled = 0      # buy orders actually filled this run (the fallback needs
                     # "no buy yet": an order the market rejected must not count)


def on_batch(state, batch):
    global peak, take_profit, trail, last_exit, buys_filled
    for fill in batch["fills"]:
        if fill.get("status") == "filled" and fill.get("side") == "buy":
            buys_filled += 1
    for bar in batch["bars"]:
        if bar["symbol"] == SYMBOL:
            closes.append(int(bar["c"]))
    if state.warming_up:
        return []
    close = state.close(SYMBOL)
    if close is None:
        return []
    held = state.positions.get(SYMBOL)

    if held is not None:
        if peak is None:
            peak = close
        peak = max(peak, close)
        value = state.value(SYMBOL, held.qty)
        if (value >= held.cost * (1 + take_profit)
                or close <= peak * (1 - trail)
                or value <= held.cost * (1 - HARD_STOP)):
            last_exit = state.seq
            peak = None
            return [{"side": "sell", "symbol": SYMBOL, "qty": held.qty,
                     "max_slippage_bps": MAX_SLIPPAGE_BPS}]
        return []

    if state.seq - last_exit <= COOLDOWN_BARS:
        return []
    amount = min(state.equity, state.cash)
    if amount < 1_000_000:  # below the 1 USDC minimum notional
        return []

    # Regime 1: capitulation bounce.
    if len(closes) > DROP_LOOKBACK:
        ret = closes[-1] / closes[-1 - DROP_LOOKBACK] - 1
        low = min(closes[-1 - DROP_LOOKBACK:-1])
        if ret <= DROP_MAX and close >= low * (1 + BOUNCE_MIN):
            peak = close
            take_profit = TAKE_PROFIT
            trail = TRAIL
            return [{"side": "buy", "symbol": SYMBOL, "notional_quote_units": amount,
                     "max_slippage_bps": MAX_SLIPPAGE_BPS}]

    # Fallback: last 4 hours, no buy filled yet.
    if buys_filled == 0 and state.seq >= FALLBACK_FROM_SEQ and len(closes) > 60:
        mom30 = closes[-1] / closes[-31] - 1
        high60 = max(closes[-1 - FALLBACK_HIGH_LOOKBACK:-1])
        if mom30 > FALLBACK_MOM or close > high60:
            peak = close
            take_profit = FALLBACK_TAKE_PROFIT
            trail = FALLBACK_TRAIL
            return [{"side": "buy", "symbol": SYMBOL, "notional_quote_units": amount,
                     "max_slippage_bps": MAX_SLIPPAGE_BPS}]

    return []


makefun_bars.run(on_batch)
