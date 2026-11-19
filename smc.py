"""Price-map tools for the gold analysis: session levels, SMC structure, FVGs and Fibonacci.

Bars are dicts with ts (broker clock), h, l and c, oldest first. The 5-1-1 setting means swings need
5 bars on each side, and a fair value gap has 1 candle on each side of its middle candle.
"""
import datetime as dt
import math

SWING = 5
ROUND_STEP = 50
FIB_RATIOS = (0.382, 0.5, 0.618)
ASIA = (2, 10)      # broker-clock hours, which run 7 hours ahead of New York
LONDON = (10, 15)


def broker_time(ts):
    return dt.datetime(1970, 1, 1) + dt.timedelta(seconds=ts)


def _span(bars):
    return (max(b["h"] for b in bars), min(b["l"] for b in bars)) if bars else None


def session_levels(bars, now_ts):
    """Yesterday's high and low, and today's Asia and London sessions, as far as each has run."""
    today = broker_time(now_ts).date()
    days = {}
    for b in bars:
        days.setdefault(broker_time(b["ts"]).date(), []).append(b)
    earlier = sorted(d for d in days if d < today)
    this_day = days.get(today, [])
    window = lambda hours: [b for b in this_day if hours[0] <= broker_time(b["ts"]).hour < hours[1]]
    return {"yesterday": _span(days[earlier[-1]]) if earlier else None,
            "asia": _span(window(ASIA)), "london": _span(window(LONDON))}


def round_levels(price, step=ROUND_STEP):
    low = math.floor(price / step) * step
    return (low, low + step) if low != price else (low - step, low + step)


def all_key_levels(sessions, price):
    out = list(round_levels(price))
    for span in sessions.values():
        if span:
            out.extend(span)
    return out


def pivots(bars, length=SWING):
    """Confirmed swings as (index, 'H' or 'L', price): nothing within length bars on either side passes them."""
    out = []
    for i in range(length, len(bars) - length):
        around = bars[i - length:i] + bars[i + 1:i + length + 1]
        if bars[i]["h"] >= max(b["h"] for b in around):
            out.append((i, "H", bars[i]["h"]))
        if bars[i]["l"] <= min(b["l"] for b in around):
            out.append((i, "L", bars[i]["l"]))
    return out


def market_structure(bars, length=SWING):
    """Trend and the last break: BOS continues the trend, CHoCH changes it. Closes decide, not wicks."""
    confirmed = {}
    for index, kind, level in pivots(bars, length):
        confirmed.setdefault(index + length, []).append((kind, level))
    high = low = None
    trend = 0
    last = None
    for i, b in enumerate(bars):
        for kind, level in confirmed.get(i, []):
            if kind == "H":
                high = level
            else:
                low = level
        if high is not None and b["c"] > high:
            last = {"kind": "CHoCH" if trend == -1 else "BOS", "side": 1, "level": high}
            trend, high = 1, None
        elif low is not None and b["c"] < low:
            last = {"kind": "CHoCH" if trend == 1 else "BOS", "side": -1, "level": low}
            trend, low = -1, None
    return {"trend": trend, "last": last}


def fair_value_gaps(bars):
    """Gaps still open: a later close through the far edge fills them. Newest last."""
    gaps = []
    for i in range(2, len(bars)):
        if bars[i]["l"] > bars[i - 2]["h"]:
            gaps.append({"side": 1, "low": bars[i - 2]["h"], "high": bars[i]["l"], "at": i})
        elif bars[i]["h"] < bars[i - 2]["l"]:
            gaps.append({"side": -1, "low": bars[i]["h"], "high": bars[i - 2]["l"], "at": i})
    open_gaps = []
    for gap in gaps:
        later = bars[gap["at"] + 1:]
        filled = any(b["c"] < gap["low"] for b in later) if gap["side"] > 0 else any(b["c"] > gap["high"] for b in later)
        if not filled:
            open_gaps.append(gap)
    return open_gaps


def nearest_gaps(gaps, price):
    """The closest open bullish gap at or under the price, and bearish gap at or over it."""
    bull = [g for g in gaps if g["side"] > 0 and g["low"] <= price]
    bear = [g for g in gaps if g["side"] < 0 and g["high"] >= price]
    return (max(bull, key=lambda g: g["high"]) if bull else None,
            min(bear, key=lambda g: g["low"]) if bear else None)


def fibonacci(bars, length=SWING):
    """Retracement of the last swing leg: a leg ending high retraces down from it, and the reverse."""
    points = pivots(bars, length)
    last_high = next((p for p in reversed(points) if p[1] == "H"), None)
    last_low = next((p for p in reversed(points) if p[1] == "L"), None)
    if not last_high or not last_low or last_high[2] <= last_low[2]:
        return None
    high, low = last_high[2], last_low[2]
    up = last_high[0] > last_low[0]
    levels = [(r, high - r * (high - low) if up else low + r * (high - low)) for r in FIB_RATIOS]
    return {"side": 1 if up else -1, "start": low if up else high, "end": high if up else low, "levels": levels}


def reaction_areas(bull, bear, fib_levels, price, tolerance):
    """The areas worth naming, nearest first: a gap, with Fibonacci when one of its levels falls on it.

    When no gap is open on a side, the nearest Fibonacci level on that side stands alone.
    """
    areas = []
    for gap, side in ((bull, -1), (bear, 1)):
        if gap:
            matched = any(gap["low"] - tolerance <= level <= gap["high"] + tolerance for level in fib_levels)
            areas.append({"price": (gap["low"] + gap["high"]) / 2, "gap": True, "fib": matched})
            continue
        beyond = [level for level in fib_levels if side * (level - price) > 0]
        if beyond:
            areas.append({"price": min(beyond, key=lambda x: abs(x - price)), "gap": False, "fib": True})
    return sorted(areas, key=lambda a: abs(a["price"] - price))


def agreement(bull, bear, fib, tolerance):
    """How gaps and Fibonacci relate: same area and direction, same area against it, or different areas.

    None when there is not enough of both to compare.
    """
    if not fib or not (bull or bear):
        return None
    levels = [level for _, level in fib["levels"]]
    for gap, side in ((bull, 1), (bear, -1)):
        if gap and any(gap["low"] - tolerance <= level <= gap["high"] + tolerance for level in levels):
            return {"state": "converge" if side == fib["side"] else "diverge", "side": side}
    return {"state": "apart", "side": 0}


def rsi(closes, period=14):
    """Wilder's RSI of the closes, or None with too little history."""
    if len(closes) <= period:
        return None
    moves = [b - a for a, b in zip(closes, closes[1:])]
    gain = sum(max(x, 0) for x in moves[:period]) / period
    loss = sum(max(-x, 0) for x in moves[:period]) / period
    for x in moves[period:]:
        gain = (gain * (period - 1) + max(x, 0)) / period
        loss = (loss * (period - 1) + max(-x, 0)) / period
    return 100.0 if loss == 0 else 100 - 100 / (1 + gain / loss)


def rsi_vote(value):
    if value is None:
        return 0
    return 1 if value > 55 else -1 if value < 45 else 0


def ema(values, period):
    k = 2.0 / (period + 1)
    out = values[0]
    for v in values[1:]:
        out = v * k + out * (1 - k)
    return out


def ma_vote(closes):
    """Up when the price is above EMA 20 and EMA 20 above EMA 50, down for the reverse."""
    fast, slow, last = ema(closes, 20), ema(closes, 50), closes[-1]
    if last > fast > slow:
        return 1
    if last < fast < slow:
        return -1
    return 0


def _last_swings(bars, kind, length):
    return [p for p in pivots(bars, length) if p[1] == kind][-2:]


def price_action_vote(bars, length=3):
    """Higher highs and higher lows are up, lower highs and lower lows are down."""
    highs, lows = _last_swings(bars, "H", length), _last_swings(bars, "L", length)
    if len(highs) < 2 or len(lows) < 2:
        return 0
    if highs[1][2] > highs[0][2] and lows[1][2] > lows[0][2]:
        return 1
    if highs[1][2] < highs[0][2] and lows[1][2] < lows[0][2]:
        return -1
    return 0


def _line_at(a, b, index):
    return a[2] + (b[2] - a[2]) * (index - a[0]) / float(b[0] - a[0])


def trend_line_vote(bars, length=3):
    """A rising line through the last two swing lows held by the close, or a falling one through two highs."""
    last = len(bars) - 1
    close = bars[-1]["c"]
    lows, highs = _last_swings(bars, "L", length), _last_swings(bars, "H", length)
    up = len(lows) == 2 and lows[1][2] > lows[0][2] and close > _line_at(lows[0], lows[1], last)
    down = len(highs) == 2 and highs[1][2] < highs[0][2] and close < _line_at(highs[0], highs[1], last)
    return int(up) - int(down)


def trend_verdict(score):
    """The five states from the votes added up."""
    if score >= 4:
        return "strong_up"
    if score >= 2:
        return "up"
    if score <= -4:
        return "strong_down"
    if score <= -2:
        return "down"
    return "none"
