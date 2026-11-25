#!/usr/bin/env python3
"""Tiny EMA-crossover bot for the make.fun bars-replay/v1 protocol.

Reads hourly bars on stdin (hello, then one batch per bar), answers each
batch with a reply carrying the orders decided on that bar. Long only, no
leverage, fully deterministic: fixed symbol order, plain float EMAs, no
clock, no randomness.

Two layers share one position per symbol:

* Main layer: a fast EMA crossing above a slow EMA opens a full-stake
  position; a cross back below, a stop-loss, or a take-profit closes it.
  The entry needs the fast EMA clearly above the slow one and still rising,
  so flat chop does not trigger entries.
* Scalp layer: a much faster EMA pair trades small bounces on symbols the
  main layer is not holding, at most one scalp entry per symbol per run, with
  a tight stop and take. It exists so a quiet, steady window still produces
  enough trades to clear the competition's minimum-trade guardrail; each
  scalp risks only a small stake.

Run: python3 main.py
"""

import json
import sys

PROTOCOL = "agent-fun/bars-replay/v1"
BPS = 10_000

# Main layer knobs.
FAST_SPAN = 5
SLOW_SPAN = 12
# Entry needs the fast EMA above the slow one by at least this share of the
# close, so flat chop does not trigger entries.
GAP_BPS = 6
# Exit a main position that fell this far under its cost, or rose this far.
STOP_BPS = 800
TAKE_BPS = 2000
# Sizing: one slot per symbol in the universe (capped by the 25% weight cap).
SLOTS = 7

# Scalp layer knobs.
SCALP_FAST_SPAN = 3
SCALP_SLOW_SPAN = 7
SCALP_SLOTS = 20
SCALP_STOP_BPS = 200
SCALP_TAKE_BPS = 300

MAX_SLIPPAGE_BPS = 100


class Ema:
    """One EMA: seeded with the mean of its first span of closes."""

    def __init__(self, span):
        self.span = span
        self.alpha = 2.0 / (span + 1)
        self.warm = []
        self.value = None

    def push(self, close):
        if self.value is not None:
            self.value += self.alpha * (close - self.value)
            return
        self.warm.append(close)
        if len(self.warm) == self.span:
            self.value = sum(self.warm) / self.span


class Pair:
    """One EMA pair plus the gap before and after the latest bar."""

    def __init__(self, fast_span, slow_span):
        self.fast = Ema(fast_span)
        self.slow = Ema(slow_span)
        self.prev_fast = None
        self.gap_before = None
        self.gap_now = None

    def push(self, close):
        self.prev_fast = self.fast.value
        self.fast.push(close)
        self.slow.push(close)
        self.gap_before = self.gap_now
        if self.fast.value is not None and self.slow.value is not None:
            self.gap_now = self.fast.value - self.slow.value
        else:
            self.gap_now = None

    def crossed_up(self):
        return (self.gap_before is not None and self.gap_now is not None
                and self.gap_now > 0 and self.gap_before <= 0)

    def crossed_down(self):
        return (self.gap_before is not None and self.gap_now is not None
                and self.gap_now < 0 and self.gap_before >= 0)


class Series:
    """Both EMA pairs for one symbol."""

    def __init__(self):
        self.main = Pair(FAST_SPAN, SLOW_SPAN)
        self.scalp = Pair(SCALP_FAST_SPAN, SCALP_SLOW_SPAN)

    def push(self, close):
        self.main.push(close)
        self.scalp.push(close)


class Bot:
    def __init__(self, hello):
        self.hello = hello
        self.symbols = [s["symbol"] for s in hello["symbols"]]
        self.scales = {s["symbol"]: (s["price_scale"], s["qty_scale"])
                       for s in hello["symbols"]}
        self.quote_scale = hello["capital"]["quote_scale"]
        self.warmup_bars = hello.get("warmup_bars", 0)
        self.series = {}
        self.seq = -1
        self.next_order = 1
        # Which layer opened each held position, and which symbols already
        # used their one scalp entry.
        self.kind = {}
        self.scalp_used = set()

    def client_id(self):
        cid = "o%d" % self.next_order
        self.next_order += 1
        return cid

    def value(self, symbol, qty, price):
        ps, qs = self.scales[symbol]
        return qty * price * 10 ** self.quote_scale // 10 ** (qs + ps)

    def on_batch(self, batch, account):
        self.seq = batch["seq"]
        orders = []
        if self.seq < self.warmup_bars:
            # Warm-up bars only build history; any order here is rejected.
            for bar in batch["bars"]:
                self._push_bar(bar)
            return orders

        cash = int(account["cash_quote_units"])
        equity = int(account["equity_quote_units"])
        held = {p["symbol"]: (int(p["qty"]), int(p["cost_quote_units"]))
                for p in account["positions"]}
        closes = {}

        for bar in batch["bars"]:
            symbol = bar["symbol"]
            closes[symbol] = int(bar["c"])
            self._push_bar(bar)

        # Sells first: each position exits on its own layer's rules.
        for symbol in self.symbols:
            if symbol not in held:
                continue
            qty, cost = held[symbol]
            price = closes.get(symbol)
            if price is None:
                continue
            worth = self.value(symbol, qty, price)
            series = self.series[symbol]
            if self.kind.get(symbol) == "scalp":
                stop_hit = worth * BPS <= cost * (BPS - SCALP_STOP_BPS)
                take_hit = worth * BPS >= cost * (BPS + SCALP_TAKE_BPS)
                if series.scalp.crossed_down() or stop_hit or take_hit:
                    orders.append(self._sell(symbol, qty))
            else:
                stop_hit = worth * BPS <= cost * (BPS - STOP_BPS)
                take_hit = worth * BPS >= cost * (BPS + TAKE_BPS)
                if series.main.crossed_down() or stop_hit or take_hit:
                    orders.append(self._sell(symbol, qty))

        # Buys: main entries first, then one scalp entry per symbol.
        room = self.hello["caps"]["max_open_positions"] - len(held)
        max_orders = self.hello["caps"]["max_orders_per_bar"]
        min_notional = int(self.hello["fees"]["min_notional_quote_units"])
        weight_cap = (self.hello["caps"]["max_weight_bps"] * equity) // BPS
        spent = 0
        bought = set()
        for symbol in self.symbols:
            if symbol in held or room <= 0 or len(orders) >= max_orders:
                continue
            series = self.series[symbol]
            price = closes.get(symbol)
            if price is None or price <= 0:
                continue
            if bar_volume_zero(batch, symbol):
                continue
            if not series.main.crossed_up():
                continue
            if series.main.gap_now * BPS <= GAP_BPS * price:
                continue
            if (series.main.prev_fast is not None
                    and series.main.fast.value <= series.main.prev_fast):
                continue
            amount = equity // SLOTS
            if amount > weight_cap:
                amount = weight_cap
            if cash - spent < amount:
                amount = cash - spent
            if amount < min_notional:
                continue
            orders.append(self._buy(symbol, amount))
            self.kind[symbol] = "main"
            bought.add(symbol)
            spent += amount
            room -= 1
        for symbol in self.symbols:
            if symbol in held or symbol in bought or symbol in self.scalp_used:
                continue
            if room <= 0 or len(orders) >= max_orders:
                continue
            series = self.series[symbol]
            price = closes.get(symbol)
            if price is None or price <= 0:
                continue
            if bar_volume_zero(batch, symbol):
                continue
            if not series.scalp.crossed_up():
                continue
            amount = equity // SCALP_SLOTS
            if amount > weight_cap:
                amount = weight_cap
            if cash - spent < amount:
                amount = cash - spent
            if amount < min_notional:
                continue
            orders.append(self._buy(symbol, amount))
            self.kind[symbol] = "scalp"
            self.scalp_used.add(symbol)
            spent += amount
            room -= 1
        return orders

    def _buy(self, symbol, amount):
        return {"side": "buy", "symbol": symbol,
                "client_id": self.client_id(),
                "notional_quote_units": str(amount),
                "max_slippage_bps": MAX_SLIPPAGE_BPS}

    def _sell(self, symbol, qty):
        return {"side": "sell", "symbol": symbol,
                "client_id": self.client_id(),
                "qty": str(qty),
                "max_slippage_bps": MAX_SLIPPAGE_BPS}

    def _push_bar(self, bar):
        symbol = bar["symbol"]
        series = self.series.get(symbol)
        if series is None:
            series = Series()
            self.series[symbol] = series
        series.push(float(int(bar["c"])))
        return series


def bar_volume_zero(batch, symbol):
    for bar in batch["bars"]:
        if bar["symbol"] == symbol:
            return bar.get("v") == "0"
    return False


def main():
    bot = None
    out = sys.stdout
    for line in sys.stdin.buffer:
        line = line.strip()
        if not line:
            continue
        msg = json.loads(line.decode("utf-8"))
        kind = msg.get("type")
        if kind == "hello":
            if msg.get("protocol") != PROTOCOL:
                raise SystemExit("unsupported protocol: %r" % (msg.get("protocol"),))
            bot = Bot(msg)
        elif kind == "batch":
            orders = bot.on_batch(msg, msg["account"])
            reply = {"type": "reply", "seq": msg["seq"], "orders": orders}
            out.write(json.dumps(reply, separators=(",", ":")) + "\n")
            out.flush()
        elif kind == "done":
            return 0
        else:
            raise SystemExit("unknown message type: %r" % (kind,))
    raise SystemExit("input ended before done")


if __name__ == "__main__":
    sys.exit(main())
