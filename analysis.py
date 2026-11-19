#!/usr/bin/env python3
"""Scheduled Persian gold analysis for Telegram: levels and scenarios from MT5, no signals, no orders.

Posts Monday to Friday from 15:30 to 00:30 Iran time. A new analysis replaces the
last one after a random 20 to 30 minutes, or sooner when a closed M5 bar triggers a scenario.
"""
import argparse
import datetime as dt
import json
import math
import os
import random
import re
import sys
import time
import urllib.request

import smc
import xau

SYMBOL = "XAUUSD.st"
STATE_FILE = os.path.join(xau.HOME, "analysis-state.json")
CALENDAR_PAGE = "https://forexfactory.live/"
CALENDAR_IMPACT = ("high",)    # red news only
FF_FEED = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"   # Forex Factory's own export; its site refuses scripts
FF_IMPACT = ("High",)
TV_URL = "https://scanner.tradingview.com/symbol?symbol=FOREXCOM:XAUUSD&no_404=true&fields=Recommend.All"
TV_NEWS = "https://news-headlines.tradingview.com/v2/headlines?client=web&lang=en&symbol=FOREXCOM:XAUUSD"
TV_CALENDAR = "https://economic-calendar.tradingview.com/events?from=%s&to=%s&countries=US"
YAHOO_GLD = "https://query1.finance.yahoo.com/v8/finance/chart/GLD?interval=5m&range=1d"
TV_MENTION_EVERY = 3       # the gauges are named in one analysis out of this many
NEWS_EARLY = 100 * 60      # about 4 analyses before an important event
NEWS_WINDOW = 30 * 60      # a high-impact event this close to now gets a warning
NEWS_SHOWN = 2
NEWS_SOURCES = ("Reuters", "Trading Economics", "Dow Jones Newswires")
NEWS_NOISE = re.compile(r"delivery|exploration|drill|mining|exports", re.I)
HEADLINE_MAX_AGE = 3 * 3600
PAUSE_BEFORE = 10 * 60     # no scenarios from this long before a red event
PAUSE_AFTER = 5 * 60       # until this long after it
EVENTS_MAX_AGE = 10 * 60
ALERT_AFTER = 3            # failed looks in a row before reporting an outage
COMMANDS = ("gold", "/gold", "گلد")
COMMAND_GAP = 60           # seconds between analyses asked for in the group
COMMAND_MAX_AGE = 120      # an older request is ignored, as after a restart
COMMAND_LOOK = 3           # seconds between checks for a request
WEEKDAYS = ("دوشنبه", "سه‌شنبه", "چهارشنبه", "پنجشنبه", "جمعه", "شنبه", "یکشنبه")
TEHRAN = dt.timedelta(hours=3, minutes=30)   # Iran has kept this offset all year since 2022
START, END = 15 * 60 + 30, 30                # minutes after midnight; the session ends the next day
DAYS = (0, 1, 2, 3, 4)                       # Monday to Friday
MIN_GAP, MAX_GAP = 20 * 60, 30 * 60
LEVELS = 5


def in_session(ts):
    t = dt.datetime(1970, 1, 1) + dt.timedelta(seconds=ts) + TEHRAN
    minute = t.hour * 60 + t.minute
    if minute >= START:
        return t.weekday() in DAYS
    return minute < END and (t.weekday() - 1) % 7 in DAYS


def fa(value, digits=0):
    text = "%.*f" % (digits, value)
    return text.translate(str.maketrans("0123456789.", "۰۱۲۳۴۵۶۷۸۹٫"))


def zone_text(zone):
    return "%s–%s" % (fa(zone[0]), fa(zone[1]))


def ema(values, period):
    k = 2.0 / (period + 1)
    out = values[0]
    for v in values[1:]:
        out = v * k + out * (1 - k)
    return out


def atr(bars, period=14):
    tail = bars[-(period + 1):]
    ranges = [max(b["h"], a["c"]) - min(b["l"], a["c"]) for a, b in zip(tail, tail[1:])]
    return sum(ranges) / len(ranges)


def swings(bars, side=2):
    """Fractal highs and lows: a bar's high or low that its neighbours on both sides do not pass."""
    levels = []
    for i in range(side, len(bars) - side):
        around = bars[i - side:i] + bars[i + 1:i + side + 1]
        if bars[i]["h"] >= max(b["h"] for b in around):
            levels.append(bars[i]["h"])
        if bars[i]["l"] <= min(b["l"] for b in around):
            levels.append(bars[i]["l"])
    return levels


def cluster(levels, gap, max_width):
    """Levels that sit close together become one zone, nearest order left to the caller."""
    zones = []
    for level in sorted(levels):
        if zones and level - zones[-1][1] <= gap and level - zones[-1][0] <= max_width:
            zones[-1][1] = level
        else:
            zones.append([level, level])
    return zones


def tidy(zones, price, below, step):
    """Whole-dollar zones that leave the price outside, nearest first, then filled out to five."""
    out = []
    for low, high in sorted(zones, key=lambda z: -z[1] if below else z[0]):
        low, high = math.floor(low), math.ceil(high)
        if below:
            high = min(high, math.ceil(price) - 1)
        else:
            low = max(low, math.floor(price) + 1)
        if low >= high:
            low, high = (high - 1, high) if below else (low, low + 1)
        if out:
            if below and high >= out[-1][0]:
                high = out[-1][0] - 1
            if not below and low <= out[-1][1]:
                low = out[-1][1] + 1
            if low >= high:
                continue
        out.append((low, high))
        if len(out) == LEVELS:
            return out
    while len(out) < LEVELS:
        if below:
            top = (out[-1][0] if out else math.ceil(price)) - step
            out.append((top - 2, top))
        else:
            bottom = (out[-1][1] if out else math.floor(price)) + step
            out.append((bottom, bottom + 2))
    return out


def find_levels(m5, m15, price, key_levels=()):
    range5 = atr(m5)
    levels = swings(m5[-300:]) + swings(m15[-200:]) + list(key_levels)
    gap, width = min(1.5, max(1.0, 0.4 * range5)), min(5.0, max(3.0, 1.5 * range5))
    below = cluster([x for x in levels if x < price], gap, width)
    above = cluster([x for x in levels if x > price], gap, width)
    step = max(2, int(round(range5)))
    return tidy(below, price, True, step), tidy(above, price, False, step)


def trend(bars):
    """(+1 up, -1 down, 0 flat or correcting, Persian name) from EMA 20 and 50."""
    closes = [b["c"] for b in bars]
    fast, slow, last = ema(closes, 20), ema(closes, 50), closes[-1]
    if abs(fast - slow) < 0.1 * atr(bars):
        return 0, "خنثی و رنج"
    if fast > slow:
        return (1, "صعودی") if last > fast else (0, "اصلاح نزولی در ساختار صعودی")
    return (-1, "نزولی") if last < fast else (0, "اصلاح صعودی در ساختار نزولی")


def ladder(zones, price, sign, range5):
    """A scenario's trigger and three targets, spaced so noise inside one candle cannot reach them.

    The trigger is the outer edge of the first zone at least 0.6 ATR from the price. Each target is
    the near edge of a later zone at least 0.5 ATR past the last, or an even step when none is left.
    """
    near, far = (0, 1) if sign > 0 else (1, 0)
    step = max(1, int(round(0.75 * range5)))
    trigger = None
    targets = []
    for zone in zones:
        if trigger is None:
            if sign * (zone[far] - price) >= 0.6 * range5:
                trigger = zone[far]
            continue
        if sign * (zone[near] - (targets[-1] if targets else trigger)) >= 0.5 * range5:
            targets.append(zone[near])
        if len(targets) == 3:
            return trigger, targets
    if trigger is None:
        trigger = int(round(price + sign * max(1, 0.6 * range5)))
    while len(targets) < 3:
        targets.append((targets[-1] if targets else trigger) + sign * step)
    return trigger, targets


def structure(bars):
    """+1 or -1 from EMA 20 against EMA 50, 0 when they sit together."""
    closes = [b["c"] for b in bars]
    fast, slow = ema(closes, 20), ema(closes, 50)
    if abs(fast - slow) < 0.1 * atr(bars):
        return 0
    return 1 if fast > slow else -1


def clean(text):
    return re.sub(r"[*_`\[\]]", "", str(text))


def tv_label(value):
    """TradingView's own gauge bands for a recommendation from -1 to 1."""
    if value is None:
        return "نامشخص"
    if value > 0.5:
        return "خرید قوی"
    if value >= 0.1:
        return "خرید"
    if value > -0.1:
        return "خنثی"
    if value >= -0.5:
        return "فروش"
    return "فروش قوی"


def _get_json(url, headers=None):
    req = urllib.request.Request(url, headers=dict({"User-Agent": "Mozilla/5.0"}, **(headers or {})))
    with xau.open_url(req, 20) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def fetch_tradingview():
    return _get_json(TV_URL)


def new_york_text(ts):
    t = xau.ny_time(ts)
    return "%s ساعت %s:%s به وقت نیویورک" % (WEEKDAYS[t.weekday()], fa(t.hour).rjust(2, "۰"), fa(t.minute).rjust(2, "۰"))


def tv_view(tv, count):
    """The gauge's direction for the bias. The message names it only now and then."""
    value = (tv or {}).get("Recommend.All")
    if value is None:
        return None
    sign = 1 if value >= 0.1 else -1 if value <= -0.1 else 0
    return {"sign": sign, "label": tv_label(value), "mention": count % TV_MENTION_EVERY == 0}


def tv_note(view, direction):
    """A line citing the gauges: always when they oppose the main direction, otherwise on its turn."""
    if not view:
        return None
    if view["sign"] and direction and view["sign"] != direction:
        return "عقربه‌های TradingView روی %s است و با جهت اصلی هم‌راستا نیست." % view["label"]
    if not view["mention"]:
        return None
    if view["sign"] and view["sign"] == direction:
        return "عقربه‌های TradingView هم روی %s است و این جهت را تأیید می‌کند." % view["label"]
    return "عقربه‌های TradingView روی %s است." % view["label"]


def parse_tv_calendar(data):
    events = []
    for e in data.get("result", []):
        if e.get("importance") == 1 and e.get("country") == "US":
            when = dt.datetime.fromisoformat(e["date"].replace("Z", "+00:00")).timestamp()
            events.append({"title": e["title"], "ts": when, "forecast": e.get("forecast"),
                           "previous": e.get("previous"), "actual": e.get("actual")})
    return events


def parse_calendar_page(page):
    """Red, high-impact dollar events with Persian names from the page's embedded data."""
    found = re.search(r'<script[^>]*id="__NUXT_DATA__"[^>]*>(.*?)</script>', page, re.S)
    data = json.loads(found.group(1))

    def value(i):
        if not isinstance(i, int) or i < 0:
            return None
        item = data[i]
        while isinstance(item, list) and len(item) == 2 and item[0] in ("Ref", "ShallowRef", "Reactive", "ShallowReactive"):
            item = data[item[1]]
        return item

    events = []
    rows = [row for row in data if isinstance(row, dict) and "event" in row and "currency_symbol" in row]
    if not rows:
        raise ValueError("the page has no calendar rows")
    for row in rows:
        if value(row["currency_symbol"]) != "USD" or value(row["impact"]) not in CALENDAR_IMPACT:
            continue
        event = value(row["event"])
        name = value(event["event_name_fa"]) or value(event["event_name"])
        stamp = dt.datetime.strptime("%s %s" % (value(row["date"]), value(row["time"])), "%Y-%m-%d %H:%M:%S")
        when = (stamp - TEHRAN - dt.datetime(1970, 1, 1)).total_seconds()
        events.append({"title": re.sub(r"\s+", " ", name).strip(), "ts": when, "forecast": value(row["forecast"]),
                       "previous": value(row["previous"]), "actual": value(row["actual"])})
    return events


def fetch_text(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with xau.open_url(req, 25) as r:
        return r.read().decode("utf-8", "replace")


def parse_ff_feed(data):
    return [{"title": e["title"], "ts": dt.datetime.fromisoformat(e["date"]).timestamp(), "forecast": e.get("forecast"),
             "previous": e.get("previous"), "actual": None}
            for e in data if e.get("country") == "USD" and e.get("impact") in FF_IMPACT]


def load_tv_calendar(now):
    stamp = lambda ts: time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(ts))
    url = TV_CALENDAR % (stamp(now - 7200), stamp(now + 2 * 86400))
    return parse_tv_calendar(_get_json(url, {"Origin": "https://www.tradingview.com",
                                             "Referer": "https://www.tradingview.com/"}))


def fetch_events(now):
    """(events, source): forexfactory.live in Persian, then Forex Factory, then TradingView, in English."""
    sources = (("live", lambda: parse_calendar_page(fetch_text(CALENDAR_PAGE))),
               ("forexfactory", lambda: parse_ff_feed(_get_json(FF_FEED))),
               ("tradingview", lambda: load_tv_calendar(now)))
    for name, load in sources:
        try:
            return load(), name
        except (OSError, ValueError, KeyError, AttributeError, TypeError, IndexError) as e:
            print("News source %s failed: %s" % (name, type(e).__name__))
    return [], None


def event_lines(events, now):
    """Important events from about 4 analyses before they happen until 30 minutes after."""
    near = sorted((e for e in events if -NEWS_WINDOW <= e["ts"] - now <= NEWS_EARLY), key=lambda e: e["ts"])[:NEWS_SHOWN]
    if not near:
        return [], None
    lines = ["*خبر مهم پیش رو*"]
    note = None
    for e in near:
        left = e["ts"] - now
        if left > 0:
            state = "%s دقیقه مانده به خبر %s" % (fa(max(1, left // 60)), clean(e["title"]))
        else:
            state = "خبر %s منتشر شد" % clean(e["title"]) + ("، واقعی %s" % clean(e["actual"]) if e.get("actual") else "")
        lines.append("• %s | %s | پیش‌بینی %s، قبلی %s"
                     % (state, new_york_text(e["ts"]),
                        clean(e.get("forecast") or "نامشخص"), clean(e.get("previous") or "نامشخص")))
        if abs(left) <= NEWS_WINDOW:
            note = "⚠️ خبر پراهمیت نزدیک است؛ نوسان شدید و شکست‌های فیک محتمل است."
    return lines + [""], note


def pick_headline(items, now, shown):
    """The newest gold headline from a major source that has not been posted yet."""
    for item in sorted(items, key=lambda x: -x.get("published", 0)):
        title = item.get("title", "")
        if (item.get("source") in NEWS_SOURCES and "gold" in title.lower() and not NEWS_NOISE.search(title)
                and now - item.get("published", 0) <= HEADLINE_MAX_AGE and item.get("id") not in shown):
            return item
    return None


def headline_lines(item):
    if not item:
        return []
    return ["*خبر TradingView*", "• %s (%s)" % (clean(item["title"]), clean(item["source"])), ""]


def parse_gld(data, now):
    """GLD's closed 5-minute bars today from Yahoo, the data the Day Trader skill reads."""
    res = data["chart"]["result"][0]
    quote = res["indicators"]["quote"][0]
    today = xau.ny_time(now).date()
    bars = []
    for i, ts in enumerate(res.get("timestamp") or []):
        h, l, c = quote["high"][i], quote["low"][i], quote["close"][i]
        t = xau.ny_time(ts)
        if None in (h, l, c) or t.date() != today or ts + 300 > now:
            continue
        bars.append({"m": t.hour * 60 + t.minute, "h": h, "l": l, "c": c})
    return bars


def day_trader_lines(bars):
    """The skill's opening-range rule on GLD: where the last closed bar sits against the first 15 minutes."""
    orng = xau.opening_range(bars)
    if not orng:
        return []
    high, low = orng
    last = bars[-1]["c"]
    if last > high:
        state = "بالای سقف"
    elif last < low:
        state = "زیر کف"
    else:
        state = "داخل محدوده"
    return ["*داده‌های Day Trader (Yahoo، صندوق طلای GLD)*",
            "سقف ۱۵ دقیقه‌ی اول: %s | کف: %s | آخرین کندل M5: %s" % (fa(high, 2), fa(low, 2), state), ""]


def pair_text(span):
    return "سقف %s | کف %s" % (fa(span[0], 2), fa(span[1], 2)) if span else "هنوز شکل نگرفته"


def key_level_lines(sessions, price):
    return ["*سطح‌های مهم*",
            "دیروز: " + pair_text(sessions["yesterday"]),
            "سشن آسیا: " + pair_text(sessions["asia"]),
            "سشن لندن: " + pair_text(sessions["london"]),
            "اعداد رند: %s | %s" % tuple(fa(x) for x in smc.round_levels(price)), ""]


def area_line(area, opposed=False):
    """One plain sentence about a price area, without the exact FVG or Fibonacci numbers."""
    where = "حوالی %s" % fa(round(area["price"]))
    if area["gap"] and area["fib"] and opposed:
        return "یک ناحیه FVG %s داریم و فیبوناچی هم به همین ناحیه می‌رسد." % where
    if area["gap"] and area["fib"]:
        return ("یک ناحیه FVG %s داریم و فیبوناچی هم همین ناحیه را نشان می‌دهد. "
                "احتمال زیادی دارد قیمت به این ناحیه واکنش نشان بدهد." % where)
    if area["gap"]:
        return "یک ناحیه FVG %s داریم که ممکن است قیمت به آن واکنش نشان بدهد." % where
    return "فیبوناچی ناحیه‌ی %s را نشان می‌دهد که ممکن است قیمت به آن واکنش نشان بدهد." % where


def agreement_line(result):
    """What the relation between gaps and Fibonacci can mean for the price, in general terms."""
    if not result:
        return None
    if result["state"] == "converge" and result["side"] > 0:
        return ("همگرایی صعودی: FVG و فیبوناچی هر دو از صعود حمایت می‌کنند. اگر قیمت به این ناحیه برگردد، "
                "احتمال ورود خریداران و برگشت رو به بالا بیشتر می‌شود. بسته‌شدن کندل زیر این ناحیه این دید را ضعیف می‌کند.")
    if result["state"] == "converge":
        return ("همگرایی نزولی: FVG و فیبوناچی هر دو از نزول حمایت می‌کنند. اگر قیمت به این ناحیه برگردد، "
                "احتمال ورود فروشندگان و برگشت رو به پایین بیشتر می‌شود. بسته‌شدن کندل بالای این ناحیه این دید را ضعیف می‌کند.")
    if result["state"] == "diverge":
        return ("واگرایی: FVG و فیبوناچی روی یک ناحیه‌اند، اما جهتشان یکی نیست. "
                "واکنش قیمت ممکن است کوتاه و پرنوسان باشد و تأیید کندل مهم‌تر می‌شود.")
    return ("همگرایی مشخصی نیست: FVG و فیبوناچی ناحیه‌های متفاوتی را نشان می‌دهند. "
            "هیچ‌کدام به‌تنهایی اثر قوی ندارد و احتمال نوسان بین این نواحی بیشتر است.")


def smc_lines(m5, price):
    """Plain sentences from structure, gaps and Fibonacci at the 5-1-1 setting, on M5."""
    view = smc.market_structure(m5)
    names = {1: "صعودی", -1: "نزولی"}
    lines = ["*ساختار و نواحی واکنش*"]
    if view["trend"]:
        lines.append("ساختار M5 %s است." % names[view["trend"]])
    bull, bear = smc.nearest_gaps(smc.fair_value_gaps(m5), price)
    fib = smc.fibonacci(m5)
    levels = [level for _, level in fib["levels"]] if fib else []
    areas = smc.reaction_areas(bull, bear, levels, price, atr(m5))
    relation = smc.agreement(bull, bear, fib, atr(m5))
    opposed = bool(relation) and relation["state"] == "diverge"
    lines += [area_line(area, opposed) for area in areas]
    summary = agreement_line(relation)
    if summary:
        lines.append(summary)
    return (lines + [""]) if len(lines) > 1 else []


VERDICTS = {"strong_up": "قیمت می‌تواند روند صعودی قوی داشته باشد.",
            "up": "قیمت روند صعودی دارد.",
            "none": "روند صعودی یا نزولی تأیید نمی‌شود.",
            "down": "قیمت روند نزولی دارد.",
            "strong_down": "قیمت می‌تواند روند نزولی قوی داشته باشد."}
VOTE_NAMES = {1: "صعودی", 0: "خنثی", -1: "نزولی"}


def indicator_votes(m5, view):
    """RSI, moving averages, price action, trend line, structure and the gauges: (RSI value, votes)."""
    closes = [b["c"] for b in m5]
    value = smc.rsi(closes)
    return value, {"rsi": smc.rsi_vote(value), "ma": smc.ma_vote(closes), "pa": smc.price_action_vote(m5),
                   "line": smc.trend_line_vote(m5), "structure": smc.market_structure(m5)["trend"],
                   "gauge": view["sign"] if view else 0}


def bias_text(score, up, down):
    """The one-hour line, from the same score as the indicator sentence."""
    if score >= 4:
        return "صعودی قوی تا زمانی که قیمت بالای %s باشد." % fa(down)
    if score >= 2:
        return "صعودی تا زمانی که قیمت بالای %s باشد." % fa(down)
    if score == 1:
        return "خنثی متمایل به صعود تا زمانی که قیمت بالای %s باشد." % fa(down)
    if score == 0:
        return "خنثی تا زمانی که قیمت بین %s و %s باشد." % (fa(down), fa(up))
    if score == -1:
        return "خنثی متمایل به نزول تا زمانی که قیمت زیر %s باشد." % fa(up)
    if score >= -3:
        return "نزولی تا زمانی که قیمت زیر %s باشد." % fa(up)
    return "نزولی قوی تا زمانی که قیمت زیر %s باشد." % fa(up)


def indicator_lines(m5, view):
    """The verdict sentence and the readings behind it."""
    value, votes = indicator_votes(m5, view)
    verdict = smc.trend_verdict(sum(votes.values()))
    state = ""
    if value is not None and value >= 70:
        state = " (اشباع خرید)"
    elif value is not None and value <= 30:
        state = " (اشباع فروش)"
    rsi_text = fa(value) + state if value is not None else "نامشخص"
    return ["*جمع‌بندی اندیکاتورها*",
            "با توجه به اندیکاتورهایی که ربات از آن‌ها استفاده می‌کند، " + VERDICTS[verdict],
            "RSI: %s | میانگین متحرک: %s | پرایس‌اکشن: %s | خط روند: %s"
            % (rsi_text, VOTE_NAMES[votes["ma"]], VOTE_NAMES[votes["pa"]], VOTE_NAMES[votes["line"]]), ""]


def pause_event(events, now):
    """The red event inside the no-scenario window, if any."""
    for e in sorted(events, key=lambda x: x["ts"]):
        if -PAUSE_AFTER <= e["ts"] - now <= PAUSE_BEFORE:
            return e
    return None


def pause_text(event, now):
    left = event["ts"] - now
    timing = "%s دقیقه مانده به خبر قرمز %s" % (fa(max(1, left // 60)), clean(event["title"])) if left > 0 \
        else "خبر قرمز %s منتشر شد" % clean(event["title"])
    return ("*XAUUSD | توقف موقت تحلیل*\n\n%s\n%s\n\n"
            "در این بازه سناریو داده نمی‌شود. منتظر انتشار خبر بمانید.\n"
            "تحلیل جدید چند دقیقه بعد از خبر ارسال می‌شود." % (timing, new_york_text(event["ts"])))


_events_cache = {"at": None, "events": []}


def cached_events(now):
    """Today's red events for the pause check, read at most every 10 minutes."""
    if _events_cache["at"] is None or now - _events_cache["at"] >= EVENTS_MAX_AGE:
        events, source = fetch_events(now)
        if source is not None:
            _events_cache.update(at=now, events=events)
    return _events_cache["events"]


def gather_extra(now, state):
    """Outside data for the message. A source that fails is left out, and the analysis still posts."""
    extra = {"lines": [], "notes": [], "tv": None, "headline": None}
    try:
        extra["lines"] += day_trader_lines(parse_gld(_get_json(YAHOO_GLD), now))
    except (OSError, ValueError, KeyError, IndexError, TypeError) as e:
        print("Yahoo skipped: %s" % type(e).__name__)
    events, source = fetch_events(now)
    lines, note = event_lines(events, now)
    extra["lines"] += lines
    if note:
        extra["notes"].append(note)
    if source in ("tradingview", None):     # TradingView's news is the last resort, after both Forex Factory sources
        try:
            item = pick_headline(_get_json(TV_NEWS).get("items", []), now, state.get("headlines", []))
            extra["lines"] += headline_lines(item)
            extra["headline"] = item["id"] if item else None
        except (OSError, ValueError, KeyError) as e:
            print("Headlines skipped: %s" % type(e).__name__)
    try:
        extra["tv"] = tv_view(fetch_tradingview(), state.get("count", 0))
    except (OSError, ValueError, KeyError) as e:
        print("Gauges skipped: %s" % type(e).__name__)
    return extra


def build(m5, m15, price, extra=None):
    """The analysis: its levels, scenario triggers and Persian text."""
    extra = extra or {"lines": [], "notes": [], "tv": None}
    sessions = smc.session_levels(m15, m5[-1]["ts"] + 300)
    supports, resistances = find_levels(m5, m15, price, smc.all_key_levels(sessions, price))
    score5, name5 = trend(m5)
    score15, name15 = trend(m15)
    view = extra.get("tv")
    score = sum(indicator_votes(m5, view)[1].values())
    range5 = atr(m5)
    up, tp_up = ladder(resistances, price, 1, range5)
    down, tp_down = ladder(supports, price, -1, range5)
    bias = bias_text(score, up, down)
    text = ["*تحلیل XAUUSD | تایم‌فریم M5 و M15*", "",
            "قیمت مبنا: %s دلار" % fa(price, 2),
            "روند کوتاه‌مدت: %s M5" % name5,
            "روند M15: %s" % name15,
            "تمایل یک ساعت آینده: %s" % bias, ""] + indicator_lines(m5, view) + extra["lines"] + smc_lines(m5, price) \
        + key_level_lines(sessions, price) + ["*حمایت‌های کلیدی*"]
    text += ["• " + zone_text(z) for z in supports]
    text += ["", "*مقاومت‌های کلیدی*"]
    text += ["• " + zone_text(z) for z in resistances]
    bear_first = name5 == "نزولی" or "اصلاح نزولی" in name5 or (name5 == "خنثی و رنج" and score < 0)
    if name5 == "نزولی":
        bear_title, bull_title = "ادامه‌ی نزول — اولویت کوتاه‌مدت", "بازگشت صعودی"
    elif name5 == "صعودی":
        bull_title, bear_title = "ادامه‌ی صعود — اولویت کوتاه‌مدت", "اصلاح نزولی"
    elif "اصلاح صعودی" in name5:
        bull_title, bear_title = "اصلاح صعودی — اولویت کوتاه‌مدت", "ادامه‌ی نزول"
    elif "اصلاح نزولی" in name5:
        bear_title, bull_title = "اصلاح نزولی — اولویت کوتاه‌مدت", "بازگشت صعودی"
    else:
        bull_title, bear_title = "شکست صعودی", "شکست نزولی"
    bear = ["با شکست %s و بسته‌شدن کندل M5 زیر آن:" % fa(down)] + \
           ["TP%d: %d" % (i + 1, tp) for i, tp in enumerate(tp_down)] + ["ابطال: بازگشت و تثبیت بالای %s" % fa(up)]
    bull = ["در صورت حفظ %s و تثبیت بالای %s:" % (fa(down), fa(up))] + \
           ["TP%d: %d" % (i + 1, tp) for i, tp in enumerate(tp_up)] + ["ابطال: شکست %s" % fa(down)]
    first_title, first_body, second_title, second_body = (bear_title, bear, bull_title, bull) if bear_first \
        else (bull_title, bull, bear_title, bear)
    text += ["", "*سناریوهای ۵ تا ۱۵ دقیقه آینده*",
             "*سناریوی اول: %s*" % first_title] + first_body + ["", "*سناریوی دوم: %s*" % second_title] + second_body
    direction = structure(m15)
    agrees = direction == structure(m5) and score5 == direction
    if direction > 0:
        summary = "جهت اصلی صعودی است و مومنتوم کوتاه‌مدت آن را تأیید می‌کند." if agrees \
            else "جهت اصلی صعودی است، اما مومنتوم فعلی ضعیف شده است."
    elif direction < 0:
        summary = "جهت اصلی نزولی است و مومنتوم کوتاه‌مدت آن را تأیید می‌کند." if agrees \
            else "جهت اصلی نزولی است، اما مومنتوم فعلی ضعیف شده است."
    else:
        summary = "جهت اصلی فعلاً مشخص نیست و بازار در حالت رنج است."
    if direction >= 0:
        wave = "شکست معتبر %s: احتمال شروع موج صعودی جدید." % fa(tp_up[1])
    else:
        wave = "شکست معتبر %s: احتمال شروع موج نزولی جدید." % fa(tp_down[1])
    key, kind = (down, "حمایت") if price - down <= up - price else (up, "مقاومت")
    gauge = tv_note(view, direction)
    text += ["", "*جمع‌بندی*", summary] + ([gauge] if gauge else []) + extra["notes"] + [
             "• زیر %s: احتمال اصلاح تا %s افزایش می‌یابد." % (fa(down), fa(tp_down[1])),
             "• بالای %s: احتمال حرکت تا %s." % (fa(up), fa(tp_up[1])),
             "• " + wave, "",
             "سطح کلیدی فعلی: %s. حفظ یا شکست این %s، جهت حرکت بعدی را مشخص می‌کند." % (fa(key), kind), "",
             "این تحلیل خودکار است، نه توصیه‌ی معامله."]
    return {"text": "\n".join(text), "up": up, "down": down}


def scenario_event(bars, state):
    """The first closed M5 bar since the last analysis that activates or breaks a scenario."""
    for b in bars:
        if b["ts"] <= state.get("bar_ts", 0):
            continue
        if b["c"] > state["up"]:
            return "سناریوی صعودی فعال شد و سناریوی نزولی نقض شد."
        if b["c"] < state["down"]:
            return "سناریوی نزولی فعال شد و سناریوی صعودی نقض شد."
    return None


def why_now(now, bars, state):
    """(post now?, reason line): a first post, a scenario event, or the random timer running out."""
    if not state.get("next_at"):
        return True, None
    reason = scenario_event(bars, state)
    if reason:
        return True, reason
    return now >= state["next_at"], None


def to_list(rates):
    return [{"ts": int(r["time"]), "h": float(r["high"]), "l": float(r["low"]), "c": float(r["close"])} for r in rates]


def read_market(term):
    info, tick = term.symbol(SYMBOL)
    closed = {}
    for name, timeframe, seconds, count in (("m5", term.mt5.TIMEFRAME_M5, 300, 400),
                                            ("m15", term.mt5.TIMEFRAME_M15, 900, 300)):
        rates = term.mt5.copy_rates_from_pos(SYMBOL, timeframe, 0, count)
        if rates is None or len(rates) < 60:
            raise xau.XauError("MT5 returned too few bars for %s" % name)
        closed[name] = [b for b in to_list(rates) if b["ts"] + seconds <= tick.time]
    return tick, closed["m5"], closed["m15"]


def load_state():
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_state(state):
    os.makedirs(xau.HOME, exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f)


def cmd_preview():
    with xau.Terminal() as term:
        tick, m5, m15 = read_market(term)
    sys.stdout.reconfigure(encoding="utf-8")
    print(build(m5, m15, (tick.bid + tick.ask) / 2, gather_extra(time.time(), {}))["text"])
    return 0


def is_gold_command(text):
    """Only the whole message counts, so talk about gold in the group stays talk."""
    word = (text or "").strip().lower().split("@")[0]
    return word in COMMANDS


def requested(updates, chat_id, now):
    """True when these Telegram updates hold a fresh gold request from the configured group."""
    for update in updates:
        message = update.get("message") or {}
        if (str((message.get("chat") or {}).get("id")) == str(chat_id) and is_gold_command(message.get("text"))
                and now - message.get("date", 0) <= COMMAND_MAX_AGE):
            return True
    return False


def read_updates(cfg, offset):
    url = "https://api.telegram.org/bot%s/getUpdates?timeout=0&allowed_updates=%%5B%%22message%%22%%5D" % cfg["token"]
    if offset is not None:
        url += "&offset=%d" % offset
    return _get_json(url).get("result", [])


def next_offset(updates, offset):
    return updates[-1]["update_id"] + 1 if updates else offset


def post_analysis(cfg, state, now, tick, m5, m15, reason=None):
    extra = gather_extra(now, state)
    made = build(m5, m15, (tick.bid + tick.ask) / 2, extra)
    text = made["text"]
    if reason:
        text = "به‌روزرسانی: %s\n\n%s" % (reason, text)
    xau.send_telegram(cfg, text, parse_mode="Markdown")
    state.update(up=made["up"], down=made["down"], bar_ts=m5[-1]["ts"],
                 next_at=now + random.randint(MIN_GAP, MAX_GAP), count=state.get("count", 0) + 1)
    if extra["headline"]:
        state["headlines"] = (state.get("headlines", []) + [extra["headline"]])[-50:]
    save_state(state)
    print("Posted. Next within %d minutes." % ((state["next_at"] - now) / 60))


def answer_request(term, cfg, state, now):
    """Post straight away at the current price, or say why not, keeping the same rules as the timer."""
    tick, m5, m15 = read_market(term)
    if abs(xau.tick_age(tick.time, now)) > xau.MAX_AGE:
        xau.send_telegram(cfg, "الان داده‌ی زنده‌ی طلا در دسترس نیست؛ بازار بسته است یا اتصال MT5 قطع شده.")
        return
    event = pause_event(cached_events(now), now)
    if event:
        xau.send_telegram(cfg, pause_text(event, now), parse_mode="Markdown")
        return
    post_analysis(cfg, state, now, tick, m5, m15)


def look(term, cfg, state, now):
    """One pass while the session is open. Raises XauError when MT5 cannot supply fresh data."""
    tick, m5, m15 = read_market(term)
    if abs(xau.tick_age(tick.time, now)) > xau.MAX_AGE:
        raise xau.XauError("the last MT5 tick is stale")
    event = pause_event(cached_events(now), now)
    if event:
        if not state.get("paused"):
            xau.send_telegram(cfg, pause_text(event, now), parse_mode="Markdown")
            state["paused"] = True
            state.pop("next_at", None)
            save_state(state)
        print("Paused for a red event.")
        return
    if state.pop("paused", None):
        state.pop("next_at", None)
    due, reason = why_now(now, m5, state)
    if not due:
        print("Analysis valid for %d more minutes." % ((state["next_at"] - now) / 60))
        return
    post_analysis(cfg, state, now, tick, m5, m15, reason)


def track_health(health, ok):
    """(message or None) after a look: one alert when MT5 stays down, one note when it comes back."""
    if ok:
        was_down = health.get("alerted")
        health.update(fails=0, alerted=False)
        return "✅ اتصال MT5 برگشت و تحلیل‌ها ادامه پیدا می‌کنند." if was_down else None
    health["fails"] = health.get("fails", 0) + 1
    if health["fails"] >= ALERT_AFTER and not health.get("alerted"):
        health["alerted"] = True
        return "⚠️ داده‌ی MT5 قطع است و فعلاً تحلیلی ارسال نمی‌شود. MT5 و اتصال حساب را بررسی کن."
    return None


def check_requests(term, cfg, state, marks, now):
    """Answer a gold request from the group, at most one a minute."""
    updates = read_updates(cfg, marks.get("offset"))
    marks["offset"] = next_offset(updates, marks.get("offset"))
    if not requested(updates, cfg["chat_id"], now):
        return
    wait = COMMAND_GAP - (now - marks.get("answered", 0))
    if wait <= 0:
        marks["answered"] = now
        marks["told"] = False
        answer_request(term, cfg, state, now)
        print("Answered a gold request.")
    elif not marks.get("told"):
        marks["told"] = True
        xau.send_telegram(cfg, "تحلیل همین الان ارسال شد. %s ثانیه دیگر دوباره بفرست." % fa(max(1, int(wait))))


def cmd_run(every):
    cfg = xau.telegram_config()
    state = load_state()
    health = {}
    marks = {}
    last_look = 0
    print("Gold analysis running: Monday to Friday, 15:30 to 00:30 Iran time. Ctrl+C stops it.")
    with xau.Terminal() as term:
        while True:
            now = time.time()
            try:
                check_requests(term, cfg, state, marks, now)
            except (xau.XauError, OSError, ValueError) as e:
                print("Gold request failed: %s" % type(e).__name__)
            if now - last_look < every:
                time.sleep(COMMAND_LOOK)
                continue
            last_look = now
            ok = True
            try:
                if in_session(now):
                    look(term, cfg, state, now)
                else:
                    state.pop("next_at", None)
                    print("Outside the session.")
            except xau.XauError as e:
                ok = False
                print("MT5 problem: %s" % e)
                try:
                    term.reconnect()
                except xau.XauError as again:
                    print("Reconnect failed: %s" % again)
            except (OSError, ValueError) as e:
                print("Will retry: %s" % type(e).__name__)
            message = track_health(health, ok)
            if message:
                try:
                    xau.send_telegram(cfg, message)
                except (OSError, ValueError, xau.XauError) as e:
                    print("Could not alert: %s" % type(e).__name__)
            time.sleep(COMMAND_LOOK)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("cmd", choices=["preview", "run"])
    ap.add_argument("--every", type=int, default=20, help="run: seconds between looks")
    args = ap.parse_args(argv)
    try:
        return cmd_preview() if args.cmd == "preview" else cmd_run(max(5, args.every))
    except xau.XauError as e:
        print("analysis.py: %s" % e, file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
