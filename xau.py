#!/usr/bin/env python3
"""trader-xau: opening-range breakout signals for gold from your MT5 terminal, long only.

Reads live quotes and 5-minute bars from the running MetaTrader 5 terminal, so
every level is on your broker's own price. It only reads: it holds no password
and has no order code. Needs `pip install MetaTrader5`.
"""
import argparse
import datetime as dt
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

OPEN, OR_END, LAST_ENTRY, FLAT = 570, 585, 930, 955   # minutes after midnight, New York
MAX_AGE = 60        # seconds before a tick counts as stale
BROKER_AHEAD_NY = 7 # broker server time minus New York time, in hours
FAST_BLOCKS = (705, 825, 930) # when each 2-hour block ends, minutes after midnight, New York
FAST_FIRST_BAR = 15   # bars into the session before the 14-bar range exists
FAST_LOOKBACK = 24    # a signal closes beyond this many bars' high or low
FAST_RELAX = 6        # bars before a block's end when a block with no signal relaxes to 1 bar
FAST_GAP = 6          # bars between signals
FAST_MAX = 3          # signals per block
FAST_TARGET_R = 2.0
FAST_TIME_BARS = 12   # a trade closes after this many bars
HOME = os.path.join(os.path.expanduser("~"), ".trader-xau")
TELEGRAM_FILE = os.path.join(HOME, "telegram.json")


class XauError(Exception):
    pass


def _first_sunday(year, month):
    d = dt.date(year, month, 1)
    return d + dt.timedelta(days=(6 - d.weekday()) % 7)


def ny_time(ts):
    """Real Unix seconds to naive New York time, by the US DST rule (no tzdata needed on Windows)."""
    utc = dt.datetime(1970, 1, 1) + dt.timedelta(seconds=ts)
    start = dt.datetime.combine(_first_sunday(utc.year, 3) + dt.timedelta(days=7), dt.time(7))
    end = dt.datetime.combine(_first_sunday(utc.year, 11), dt.time(6))
    return utc - dt.timedelta(hours=4 if start <= utc < end else 5)


def broker_to_ny(ts):
    """MT5 bar and tick times are server clock readings, not real Unix seconds."""
    return dt.datetime(1970, 1, 1) + dt.timedelta(seconds=int(ts) - BROKER_AHEAD_NY * 3600)


def tick_age(tick_ts, now):
    return (ny_time(now) - broker_to_ny(tick_ts)).total_seconds()


def to_bars(rates, point):
    """MT5 rates to the New York cash session; each bar is {ts, day, m, o, h, l, c, spread}. Prices are bids."""
    bars = []
    for r in rates:
        t = broker_to_ny(r["time"])
        m = t.hour * 60 + t.minute
        if OPEN <= m < 960:
            bars.append({"ts": int(r["time"]), "day": t.date().isoformat(), "m": m,
                         "o": float(r["open"]), "h": float(r["high"]), "l": float(r["low"]),
                         "c": float(r["close"]), "spread": int(r["spread"]) * point})
    return bars


def opening_range(bars):
    rng = [b for b in bars if OPEN <= b["m"] < OR_END]
    if len(rng) < 3:
        return None
    return max(b["h"] for b in rng), min(b["l"] for b in rng)


def find_signal(bars):
    """The state of one session from its CLOSED bars."""
    orng = opening_range(bars)
    if not orng:
        return {"status": "BUILDING"}
    high, low = orng
    mid = (high + low) / 2
    out = {"status": "WAITING", "or_high": high, "or_low": low, "mid": mid}
    for i, b in enumerate(bars):
        if b["m"] < OR_END or b["m"] + 5 > LAST_ENTRY:
            continue
        if b["c"] > high:
            target = b["c"] + 2 * (b["c"] - mid)
            out.update(status="TRIGGERED", bar=b, target=target)
            for later in bars[i + 1:]:
                if later["l"] <= mid:
                    out["status"] = "STOPPED"
                    break
                if later["h"] >= target:
                    out["status"] = "TARGET_HIT"
                    break
            break
    return out


def size(balance, max_loss, ask, stop, contract, step, leverage):
    """Whole volume steps whose loss at the stop fits max_loss and whose margin fits the balance."""
    oz = contract * step
    steps = int((max_loss + 1e-9) // ((ask - stop) * oz))
    steps = min(steps, int(balance * leverage // (ask * oz)))
    return {"steps": steps, "lots": round(steps * step, 2), "loss": steps * (ask - stop) * oz,
            "notional": steps * oz * ask, "margin": steps * oz * ask / leverage}


def plan(sig, bid, ask, contract, step, args):
    """The trade from the live ask. The stop and target trigger on the bid, so the spread is in the risk."""
    spread = ask - bid
    stop = round(sig["mid"], 2)
    risk = ask - stop
    if risk <= 0:
        return {"ok": False, "why": "price is already at or under the stop"}
    planned = sig["bar"]["c"] - sig["mid"]
    if risk > 1.5 * planned:
        return {"ok": False, "why": "too late: price ran $%.2f past the signal" % (risk - planned)}
    if risk < 2 * spread:
        return {"ok": False, "why": "stop of $%.2f is too tight for a $%.2f spread" % (risk, spread)}
    sized = size(args.balance, args.max_loss, ask, stop, contract, step, args.leverage)
    if sized["steps"] < 1:
        return {"ok": False, "why": "the smallest size would lose $%.2f at the stop, over your $%.2f limit"
                                    % (risk * contract * step, args.max_loss)}
    target = round(ask + 2 * risk, 2)
    return dict(sized, ok=True, entry=ask, stop=stop, target=target, stop_dist=risk,
                target_dist=target - ask, reward=sized["steps"] * contract * step * (target - ask))


def backtest(days, contract, step, max_loss):
    """Replay per session: buy at the next bar's open plus its spread, stop first when a bar hits both."""
    trades = []
    for day in sorted(days):
        bars = days[day]
        orng = opening_range(bars)
        if not orng:
            continue
        high, low = orng
        stop = (high + low) / 2
        for i, b in enumerate(bars):
            if b["m"] < OR_END or b["m"] + 5 > LAST_ENTRY or b["c"] <= high:
                continue
            if i + 1 >= len(bars):
                break
            nxt = bars[i + 1]
            entry = nxt["o"] + nxt["spread"]
            risk = entry - stop
            if risk < 2 * nxt["spread"] or risk <= 0:
                break
            target = entry + 2 * risk
            exit_px, outcome = None, "flat"
            for later in bars[i + 1:]:
                if later["m"] + 5 > FLAT:
                    break
                exit_px = later["c"]
                if later["l"] <= stop:
                    exit_px, outcome = stop, "stop"
                    break
                if later["h"] >= target:
                    exit_px, outcome = target, "target"
                    break
            if exit_px is None:
                break
            oz = contract * step
            steps = int((max_loss + 1e-9) // (risk * oz))
            trades.append({"day": day, "outcome": outcome, "r": (exit_px - entry) / risk,
                           "usd": steps * oz * (exit_px - entry), "steps": steps})
            break
    return trades


def summarize(trades):
    if not trades:
        return {"trades": 0}
    total = peak = drawdown = 0.0
    for t in trades:
        total += t["usd"]
        peak = max(peak, total)
        drawdown = max(drawdown, peak - total)
    wins = sum(t["r"] > 0 for t in trades)
    return {"trades": len(trades), "wins": wins, "win_pct": 100.0 * wins / len(trades),
            "avg_r": sum(t["r"] for t in trades) / len(trades),
            "usd": total, "max_drawdown": drawdown,
            "stops": sum(t["outcome"] == "stop" for t in trades),
            "targets": sum(t["outcome"] == "target" for t in trades),
            "flats": sum(t["outcome"] == "flat" for t in trades),
            "too_wide": sum(t["steps"] == 0 for t in trades)}


def block_of(minute):
    return min((minute - OR_END) // 120, len(FAST_BLOCKS) - 1)


def average_range(bars, i, period=14):
    if i < period:
        return None
    return sum(max(bars[j]["h"], bars[j - 1]["c"]) - min(bars[j]["l"], bars[j - 1]["c"])
               for j in range(i - period + 1, i + 1)) / period


def replay(bars, final=False):
    """The fast rule over one session: one trade at a time, 1 to 3 signals per 2-hour block, buy or sell.

    A trade whose next bar has not closed yet has outcome "pending"; one still running has "open".
    With final=True a session's last bar closes any running trade.
    """
    trades, counts = [], {}
    i, free, last = FAST_FIRST_BAR, 0, -99
    while i < len(bars):
        b = bars[i]
        if b["m"] < OR_END or b["m"] + 5 > LAST_ENTRY:
            i += 1
            continue
        block = block_of(b["m"])
        count = counts.get(block, 0)
        left = (FAST_BLOCKS[block] - (b["m"] + 5)) // 5
        if i < free or i - last < FAST_GAP or count >= FAST_MAX:
            i += 1
            continue
        relaxed = count == 0 and left <= FAST_RELAX
        n = 1 if relaxed else FAST_LOOKBACK
        prior = bars[max(0, i - n):i]
        side = 1 if b["c"] > max(x["h"] for x in prior) else -1 if b["c"] < min(x["l"] for x in prior) else 0
        if not side and count == 0 and left <= 0:
            side = 1 if b["c"] >= b["o"] else -1
        atr = average_range(bars, i)
        if not side or not atr:
            i += 1
            continue
        t = {"ts": b["ts"], "m": b["m"], "side": side, "forced": relaxed, "atr": atr, "outcome": "pending"}
        trades.append(t)
        counts[block] = count + 1
        last = i
        if i + 1 >= len(bars):
            break
        nxt = bars[i + 1]
        t["entry"] = nxt["o"] + nxt["spread"] if side > 0 else nxt["o"]
        stop, target = t["entry"] - side * atr, t["entry"] + side * FAST_TARGET_R * atr
        t["outcome"] = "open"
        j = i + 1
        for j in range(i + 1, len(bars)):
            q = bars[j]
            ask_h, ask_l = q["h"] + q["spread"], q["l"] + q["spread"]
            hit_stop = q["l"] <= stop if side > 0 else ask_h >= stop
            hit_target = q["h"] >= target if side > 0 else ask_l <= target
            if hit_stop or hit_target:
                t["outcome"], t["exit"] = ("stop", stop) if hit_stop else ("target", target)
                break
            if j - i >= FAST_TIME_BARS or q["m"] + 5 > FLAT or (final and j == len(bars) - 1):
                t["outcome"], t["exit"] = "flat", q["c"] + (q["spread"] if side < 0 else 0)
                break
        if t["outcome"] == "open":
            break
        free = j + 1
        i += 1
    return trades


def fast_backtest(days, contract, step, max_loss, slippage=0.0):
    trades = []
    oz = contract * step
    for day in sorted(days):
        for t in replay(days[day], final=True):
            if "exit" not in t:
                continue
            move = t["side"] * (t["exit"] - t["entry"]) - slippage
            steps = int((max_loss + 1e-9) // (t["atr"] * oz))
            trades.append({"day": day, "outcome": t["outcome"], "side": t["side"], "forced": t["forced"],
                           "r": move / t["atr"], "usd": steps * oz * move, "steps": steps})
    return trades


def fast_plan(t, bid, ask, contract, step, args):
    """The levels from the live price: entry at the ask to buy, at the bid to sell."""
    entry = ask if t["side"] > 0 else bid
    sized = size(args.balance, args.max_loss, entry, entry - t["atr"], contract, step, args.leverage)
    if sized["steps"] < 1:
        return None
    reach = FAST_TARGET_R * t["atr"]
    until = min(t["m"] + 5 * FAST_TIME_BARS + 5, FLAT)
    return dict(sized, entry=entry, stop=round(entry - t["side"] * t["atr"], 2),
                target=round(entry + t["side"] * reach, 2), stop_dist=t["atr"], target_dist=reach,
                reward=sized["steps"] * contract * step * reach,
                until="%d:%02d" % (until // 60, until % 60))


def fast_messages(trades, day, symbol, plan_for, state):
    """New messages for this look, updating state. A signal found late is skipped, never sent stale."""
    if state.get("day") != day:
        state.clear()
        state.update(day=day, seen=[], live=[], closed=[])
    out = []
    for t in trades:
        key = str(t["ts"])
        if key not in state["seen"]:
            state["seen"].append(key)
            p = plan_for(t) if t["outcome"] == "pending" else None
            if p:
                state["live"].append(key)
                out.append("سیگنال %s %s\nورود: %.2f\nحد ضرر: %.2f (%.2f فاصله)\nهدف: %.2f (%.2f فاصله)\n"
                           "حجم: %.2f لات\nضرر در حد ضرر: %.2f$\nسود در هدف: %.2f$\n"
                           "اگر تا %s نیویورک باز ماند، ببند\nقانون خودکار است، نه توصیه‌ی سرمایه‌گذاری."
                           % ("خرید" if t["side"] > 0 else "فروش", symbol, p["entry"], p["stop"], p["stop_dist"],
                              p["target"], p["target_dist"], p["lots"], p["loss"], p["reward"], p["until"]))
        if key in state["live"] and key not in state["closed"] and t["outcome"] in ("stop", "target", "flat"):
            state["closed"].append(key)
            out.append({"stop": "سیگنال قبلی %s به حد ضرر خورد.", "target": "سیگنال قبلی %s به هدف رسید.",
                        "flat": "زمان سیگنال قبلی %s تمام شد. اگر باز است، ببند."}[t["outcome"]] % symbol)
    return out


def cmd_fast_check(args, term):
    info, tick = term.symbol(args.symbol)
    age = tick_age(tick.time, time.time())
    print("%s  %s bid %.2f  ask %.2f" % (broker_to_ny(tick.time).strftime("%a %H:%M:%S NY"), args.symbol, tick.bid, tick.ask))
    if abs(age) > MAX_AGE:
        print("STALE: the last tick is %.0f seconds old, so the market is shut or MT5 is offline. No signal." % age)
        return 1
    trades = fast_today(args, term, info, tick)
    if trades and trades[-1]["outcome"] == "pending":
        t = trades[-1]
        p = fast_plan(t, tick.bid, tick.ask, info.trade_contract_size, info.volume_step, args)
        if p:
            print("SIGNAL (%s): entry %.2f  stop %.2f  target %.2f  %.2f lot  close by %s NY"
                  % ("buy" if t["side"] > 0 else "sell", p["entry"], p["stop"], p["target"], p["lots"], p["until"]))
            return 0
    print("NO NEW SIGNAL: %d signals so far today." % len(trades))
    return 1


def fast_today(args, term, info, tick):
    if args.leverage is None:
        args.leverage = term.mt5.account_info().leverage
    today = broker_to_ny(tick.time).date().isoformat()
    return replay([b for b in to_bars(term.rates(args.symbol, 500), info.point)
                   if b["day"] == today and b["ts"] + 300 <= tick.time])


def cmd_fast_backtest(args, term):
    info, _ = term.symbol(args.symbol)
    days = {}
    for b in to_bars(term.rates(args.symbol, 100000), info.point):
        days.setdefault(b["day"], []).append(b)
    trades = fast_backtest(days, info.trade_contract_size, info.volume_step, args.max_loss, args.slippage)
    s = summarize(trades)
    print("%d sessions, %s M5 bars, max loss $%.2f, slippage $%.2f per trade"
          % (len(days), args.symbol, args.max_loss, args.slippage))
    if not s["trades"]:
        print("No trades.")
        return 1
    print("%d trades: %d targets, %d stops, %d closed on time" % (s["trades"], s["targets"], s["stops"], s["flats"]))
    print("Winners %d (%.0f%%), average %+.3fR" % (s["wins"], s["win_pct"], s["avg_r"]))
    print("Total %+.2f dollars, worst drawdown %.2f dollars" % (s["usd"], s["max_drawdown"]))
    half = len(trades) // 2
    for name, part in (("First half", trades[:half]), ("Second half", trades[half:]),
                       ("Buys", [t for t in trades if t["side"] > 0]), ("Sells", [t for t in trades if t["side"] < 0]),
                       ("Weak entries", [t for t in trades if t["forced"]]),
                       ("Normal entries", [t for t in trades if not t["forced"]])):
        p = summarize(part)
        if p["trades"]:
            print("%s: %d trades, average %+.3fR, %+.2f dollars" % (name, p["trades"], p["avg_r"], p["usd"]))
    print("Spread is included. Commission is not.")
    return 0


def cmd_fast_watch(args, term):
    cfg = telegram_config()
    state = load_state(args.symbol)
    print("Watching %s every %ds with the fast rule. Ctrl+C stops it." % (args.symbol, args.every))
    while True:
        try:
            info, tick = term.symbol(args.symbol)
            if abs(tick_age(tick.time, time.time())) > MAX_AGE:
                print("%s: market shut or MT5 offline." % broker_to_ny(tick.time).strftime("%a %H:%M NY"))
            else:
                trades = fast_today(args, term, info, tick)
                plan_for = lambda t: fast_plan(t, tick.bid, tick.ask, info.trade_contract_size, info.volume_step, args)
                before = json.loads(json.dumps(state))
                messages = fast_messages(trades, broker_to_ny(tick.time).date().isoformat(), args.symbol, plan_for, state)
                try:
                    for message in messages:
                        send_telegram(cfg, message)
                except Exception:
                    state.clear()
                    state.update(before)
                    raise
                save_state(args.symbol, state)
                print("%s: %d signals today, %d sent now."
                      % (broker_to_ny(tick.time).strftime("%H:%M NY"), len(trades), len(messages)))
        except (XauError, OSError, ValueError) as e:
            print("Will retry: %s" % e)
        time.sleep(args.every)


def connect(mt5):
    """Attach to MT5. A portable copy named in TRADER_MT5 is started in portable mode if it is closed."""
    path = os.environ.get("TRADER_MT5")
    if path:
        return mt5.initialize(path=path, portable=True, timeout=30000)
    return mt5.initialize(timeout=30000)


class Terminal:
    """Read-only access to the MT5 terminal that is already open and logged in."""

    def __enter__(self):
        try:
            import MetaTrader5 as mt5
        except ImportError:
            raise XauError("the MetaTrader5 package is missing: pip install MetaTrader5")
        self.mt5 = mt5
        if not connect(mt5):
            raise XauError("MT5 will not connect (%s). Is it open and logged in?" % (mt5.last_error(),))
        return self

    def __exit__(self, *exc):
        self.mt5.shutdown()

    def reconnect(self):
        self.mt5.shutdown()
        if not connect(self.mt5):
            raise XauError("MT5 will not connect (%s). Is it open and logged in?" % (self.mt5.last_error(),))

    def symbol(self, name):
        if not self.mt5.symbol_select(name, True):
            raise XauError("MT5 has no symbol %s" % name)
        info, tick = self.mt5.symbol_info(name), self.mt5.symbol_info_tick(name)
        if info is None or tick is None:
            raise XauError("MT5 has no quote for %s" % name)
        return info, tick

    def rates(self, name, count):
        for n in (count, 50000, 20000, 5000):
            if n <= count:
                r = self.mt5.copy_rates_from_pos(name, self.mt5.TIMEFRAME_M5, 0, n)
                if r is not None and len(r):
                    return r
        raise XauError("MT5 returned no 5-minute bars for %s: %s" % (name, self.mt5.last_error()))


def cmd_price(args, term):
    _, tick = term.symbol(args.symbol)
    age = tick_age(tick.time, time.time())
    print("%s bid %.2f  ask %.2f  spread %.2f  %.0fs old"
          % (args.symbol, tick.bid, tick.ask, tick.ask - tick.bid, age))
    return 0


def evaluate(args, term):
    """One look at the market: {status, lines, day, minute, bar_time, plan}."""
    info, tick = term.symbol(args.symbol)
    now = time.time()
    t = broker_to_ny(tick.time)
    age = tick_age(tick.time, now)
    out = {"status": "", "symbol": args.symbol, "day": t.date().isoformat(), "minute": t.hour * 60 + t.minute,
           "bar_time": None, "plan": None,
           "lines": ["%s NY  %s bid %.2f  ask %.2f  spread %.2f"
                     % (ny_time(now).strftime("%a %H:%M:%S"), args.symbol, tick.bid, tick.ask, tick.ask - tick.bid)]}
    lines = out["lines"]
    if abs(age) > MAX_AGE:
        out["status"] = "STALE"
        lines.append("STALE: the last tick is %.0f seconds old, so the market is shut or MT5 is offline. No signal." % age)
        return out
    if args.leverage is None:
        args.leverage = term.mt5.account_info().leverage
    closed = [b for b in to_bars(term.rates(args.symbol, 500), info.point)
              if b["day"] == out["day"] and b["ts"] + 300 <= tick.time]
    sig = find_signal(closed)
    out["status"] = sig["status"]
    if sig["status"] == "BUILDING":
        lines.append("BUILDING: the opening range ends at 9:45 NY.")
        return out
    lines.append("Opening range: high %.2f  low %.2f" % (sig["or_high"], sig["or_low"]))
    if sig["status"] == "WAITING":
        if out["minute"] >= LAST_ENTRY:
            lines.append("EXPIRED: no breakout before 15:30 NY. No trade today.")
        else:
            lines.append("WAITING: needs a 5-minute close above %.2f, %.2f away."
                         % (sig["or_high"], sig["or_high"] - tick.bid))
        return out
    bar_time = out["bar_time"] = "%d:%02d" % (sig["bar"]["m"] // 60, sig["bar"]["m"] % 60)
    if sig["status"] != "TRIGGERED":
        lines.append("%s: the %s signal already finished. No new trade today."
                     % ("STOPPED" if sig["status"] == "STOPPED" else "TARGET HIT", bar_time))
        return out
    p = plan(sig, tick.bid, tick.ask, info.trade_contract_size, info.volume_step, args)
    if not p["ok"]:
        out["status"] = "NO_TRADE"
        lines.append("NO TRADE: signal at %s, but %s." % (bar_time, p["why"]))
        return out
    out["status"], out["plan"] = "SIGNAL", p
    lines.append("SIGNAL (buy) at %s NY" % bar_time)
    lines.append("  Entry  %.2f" % p["entry"])
    lines.append("  Stop   %.2f  ($%.2f below)" % (p["stop"], p["stop_dist"]))
    lines.append("  Target %.2f  ($%.2f above)" % (p["target"], p["target_dist"]))
    lines.append("  Size   %.2f lot" % p["lots"])
    lines.append("  Loss at stop $%.2f, gain at target $%.2f" % (p["loss"], p["reward"]))
    lines.append("  Position $%.0f, margin $%.0f at 1:%d" % (p["notional"], p["margin"], args.leverage))
    lines.append("  Close by 15:55 NY. This tool places no order.")
    return out


def cmd_check(args, term):
    result = evaluate(args, term)
    print("\n".join(result["lines"]))
    return 0 if result["status"] == "SIGNAL" else 1


def telegram_config():
    """The token and chat id, from the environment or the local config file, never the command line."""
    cfg = {}
    if os.path.exists(TELEGRAM_FILE):
        with open(TELEGRAM_FILE, encoding="utf-8") as f:
            cfg = json.load(f)
    token = os.environ.get("TELEGRAM_BOT_TOKEN") or cfg.get("token")
    chat = os.environ.get("TELEGRAM_CHAT_ID") or cfg.get("chat_id")
    if not token or not chat:
        raise XauError("Telegram is not set up: put token and chat_id in %s" % TELEGRAM_FILE)
    return {"token": str(token), "chat_id": str(chat)}


def open_url(request, timeout):
    """Read the system proxy on every call, because a VPN can change it while the bots run."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler(urllib.request.getproxies()))
    return opener.open(request, timeout=timeout)


def migrated_chat(error_body):
    """The group's new id when Telegram says it was upgraded to a supergroup, else None."""
    try:
        return json.loads(error_body).get("parameters", {}).get("migrate_to_chat_id")
    except ValueError:
        return None


def remember_chat(chat_id):
    with open(TELEGRAM_FILE, encoding="utf-8-sig") as f:
        saved = json.load(f)
    saved["chat_id"] = chat_id
    with open(TELEGRAM_FILE, "w", encoding="utf-8") as f:
        json.dump(saved, f)


def send_telegram(cfg, text, parse_mode=None):
    fields = {"chat_id": cfg["chat_id"], "text": text}
    if parse_mode:
        fields["parse_mode"] = parse_mode
    body = urllib.parse.urlencode(fields).encode("utf-8")
    url = "https://api.telegram.org/bot%s/sendMessage" % cfg["token"]
    try:
        with open_url(urllib.request.Request(url, data=body), 20) as r:
            if not json.loads(r.read().decode("utf-8")).get("ok"):
                raise XauError("Telegram refused the message")
    except urllib.error.HTTPError as e:
        moved = migrated_chat(e.read().decode("utf-8", "replace"))
        if not moved:
            raise XauError("Telegram refused the message (HTTP %d)" % e.code)
        cfg["chat_id"] = str(moved)
        remember_chat(moved)
        send_telegram(cfg, text, parse_mode)


def next_message(result, state):
    """The message this look calls for, if any, updating state. Each one goes out once a day."""
    day = result["day"]
    if state.get("day") != day:
        state.clear()
        state["day"] = day
    if result["status"] == "SIGNAL" and not state.get("signal"):
        state["signal"] = True
        p = result["plan"]
        return ("سیگنال خرید %s\n"
                "ورود: %.2f\nحد ضرر: %.2f (%.2f پایین‌تر)\nهدف: %.2f (%.2f بالاتر)\n"
                "حجم: %.2f لات\nضرر در حد ضرر: %.2f$\nسود در هدف: %.2f$\n"
                "بستن تا ۱۵:۵۵ نیویورک\nقانون خودکار است، نه توصیه‌ی سرمایه‌گذاری."
                % (result["symbol"], p["entry"], p["stop"], p["stop_dist"], p["target"], p["target_dist"],
                   p["lots"], p["loss"], p["reward"]))
    if state.get("signal") and not state.get("closed"):
        if result["status"] == "STOPPED":
            state["closed"] = True
            return "سیگنال امروز %s به حد ضرر خورد." % result["symbol"]
        if result["status"] == "TARGET_HIT":
            state["closed"] = True
            return "سیگنال امروز %s به هدف رسید." % result["symbol"]
        if result["minute"] >= FLAT - 5:
            state["closed"] = True
            return "وقت بستن معامله‌ی امروز %s است (۱۵:۵۵ نیویورک)." % result["symbol"]
    return None


def state_path(symbol):
    return os.path.join(HOME, "state-%s.json" % symbol)


def load_state(symbol):
    try:
        with open(state_path(symbol), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_state(symbol, state):
    os.makedirs(HOME, exist_ok=True)
    with open(state_path(symbol), "w", encoding="utf-8") as f:
        json.dump(state, f)


def cmd_telegram_test(args, term):
    send_telegram(telegram_config(), "پیام آزمایشی trader-xau: اتصال تلگرام کار می‌کند.")
    print("Sent.")
    return 0


def cmd_watch(args, term):
    cfg = telegram_config()
    state = load_state(args.symbol)
    print("Watching %s every %ds. Ctrl+C stops it." % (args.symbol, args.every))
    while True:
        try:
            result = evaluate(args, term)
            print(result["lines"][-1] if result["status"] != "SIGNAL" else "SIGNAL")
            before = dict(state)
            message = next_message(result, state)
            if message:
                try:
                    send_telegram(cfg, message)
                except Exception:
                    state.clear()
                    state.update(before)
                    raise
                save_state(args.symbol, state)
                print("Sent to Telegram.")
        except (XauError, OSError, ValueError) as e:
            print("Will retry: %s" % e)
        time.sleep(args.every)


def cmd_backtest(args, term):
    info, _ = term.symbol(args.symbol)
    bars = to_bars(term.rates(args.symbol, 100000), info.point)
    days = {}
    for b in bars:
        days.setdefault(b["day"], []).append(b)
    trades = backtest(days, info.trade_contract_size, info.volume_step, args.max_loss)
    s = summarize(trades)
    span = "%s to %s" % (min(days), max(days)) if days else "no data"
    print("%d sessions, %s, %s M5 bars, max loss $%.2f" % (len(days), span, args.symbol, args.max_loss))
    if not s["trades"]:
        print("No trades.")
        return 1
    print("%d trades: %d targets, %d stops, %d closed at the end of the day"
          % (s["trades"], s["targets"], s["stops"], s["flats"]))
    print("Winners %d (%.0f%%), average %+.2fR" % (s["wins"], s["win_pct"], s["avg_r"]))
    print("Total %+.2f dollars, worst drawdown %.2f dollars" % (s["usd"], s["max_drawdown"]))
    half = len(trades) // 2
    for name, part in (("First half", trades[:half]), ("Second half", trades[half:])):
        p = summarize(part)
        if p["trades"]:
            print("%s: %d trades, average %+.2fR, %+.2f dollars" % (name, p["trades"], p["avg_r"], p["usd"]))
    if s["too_wide"]:
        print("%d signals had a stop too wide for the smallest size and count as $0." % s["too_wide"])
    print("Spread is included. Commission and slippage are not.")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("cmd", choices=["price", "check", "backtest", "watch", "telegram-test"])
    ap.add_argument("--symbol", default="XAUUSD.st")
    ap.add_argument("--balance", type=float, default=1000.0)
    ap.add_argument("--max-loss", type=float, default=None, help="dollars lost at the stop, per trade")
    ap.add_argument("--leverage", type=int, default=None, help="default: the MT5 account's leverage")
    ap.add_argument("--every", type=int, default=30, help="watch: seconds between looks")
    ap.add_argument("--rule", choices=["orb", "fast"], default="orb",
                    help="orb: one buy signal a day. fast: 1 to 3 buy or sell signals per 2 hours")
    ap.add_argument("--slippage", type=float, default=0.0, help="fast backtest: dollars lost per trade")
    args = ap.parse_args(argv)
    if args.max_loss is None:
        if args.cmd == "watch":
            ap.error("watch needs --max-loss, because the lot size in each message depends on it")
        args.max_loss = 10.0
    if args.balance <= 0 or args.max_loss <= 0 or (args.leverage is not None and args.leverage <= 0):
        ap.error("--balance, --max-loss and --leverage must be above 0")
    if args.every < 5:
        ap.error("--every must be at least 5")
    try:
        if args.cmd == "telegram-test":
            return cmd_telegram_test(args, None)
        with Terminal() as term:
            commands = {"price": cmd_price, "check": cmd_check, "backtest": cmd_backtest, "watch": cmd_watch}
            if args.rule == "fast":
                commands.update(check=cmd_fast_check, backtest=cmd_fast_backtest, watch=cmd_fast_watch)
            return commands[args.cmd](args, term)
    except XauError as e:
        print("xau.py: %s" % e, file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 0
    except OSError as e:
        print("xau.py: network error: %s" % type(e).__name__, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
