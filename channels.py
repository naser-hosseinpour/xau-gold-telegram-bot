#!/usr/bin/env python3
"""Announce to the Telegram group when a watched public channel posts a live, a signal or an analysis.

Reads each channel's public t.me/s/ page about once a minute and posts one short line with a link
to the source post. It never copies the post itself. Standard library only.
"""
import argparse
import datetime as dt
import html
import json
import os
import re
import sys
import time
import urllib.request

import xau

CHANNELS_FILE = os.path.join(xau.HOME, "channels.json")


def load_channels(path=None):
    """[(display name, public handle)] from channels.json, e.g. [["Analyst", "somechannel"]]; empty if missing."""
    try:
        with open(path or CHANNELS_FILE, encoding="utf-8") as f:
            return [tuple(c) for c in json.load(f)]
    except (OSError, ValueError):
        return []


CHANNELS = load_channels()
STATE_FILE = os.path.join(xau.HOME, "channels-state.json")
UA = "Mozilla/5.0 (trader-xau channel watcher)"
MAX_POST_AGE = 2 * 3600                       # a post older than this was missed, so it stays silent
COOLDOWN = {"live": 30 * 60, "analysis": 15 * 60, "signal": 0}   # seconds before the same news repeats
LABEL = {"live": ("🔴", "لایو گذاشته"), "signal": ("📢", "سیگنال گذاشته"), "analysis": ("📊", "تحلیل گذاشته")}

SKIP = re.compile(r"تقویم اقتصادی|🛑 *واقعی|فیک|معرفی صرافی|هشدار مدیریت ریسک|استاد|سودم|پیپ|ممنون|سپاس|دیدی تحلیل"
                  r"|شات سود|ضرر کرده|ضررهای|تشکر|بر+کت|لایو شما|لایو دادم")
PAST_LIVE = re.compile(r"لایو بود|لایو[^.]{0,20}گفتم")
LIVE = re.compile(r"لایو[\s\u200c]*(ترید|هستیم|خواهیم|شروع|داریم)|\blive\b|youtube\.com/live|meet\.google\.com", re.I)
SIGNAL = re.compile(r"\b(buy|sell|tp\s*\d*|sl)\b|پوزیشن (منتورینگ|جدید|باز)|نقطه ورود|ورود بعدی|حد ضرر|سیگنال", re.I)
ANALYSIS = re.compile(r"تحلیل|زون|حمایت|مقاومت|سناریو|ساختار|آپدیت طلا|بازار طلا")


def classify(text):
    """live, signal, analysis or None for one post's text."""
    if not text or SKIP.search(text):
        return None
    if LIVE.search(text) and not PAST_LIVE.search(text):
        return "live"
    if SIGNAL.search(text):
        return "signal"
    if ANALYSIS.search(text):
        return "analysis"
    return None


def parse_posts(page):
    """[{id, time, text}] from a t.me/s/ page, oldest first."""
    posts = []
    for part in re.split(r'(?=<div class="tgme_widget_message_wrap)', page)[1:]:
        post = re.search(r'data-post="[^"/]+/(\d+)"', part)
        when = re.search(r'<time[^>]*datetime="([^"]+)"', part)
        body = re.search(r'tgme_widget_message_text[^>]*>(.*?)</div>', part, re.S)
        if not post or not when:
            continue
        text = ""
        if body:
            links = " ".join(re.findall(r'href="([^"]+)"', body.group(1)))
            text = html.unescape(re.sub(r"<[^>]+>", " ", body.group(1))) + " " + links
        stamp = dt.datetime.strptime(when.group(1)[:19], "%Y-%m-%dT%H:%M:%S")
        posts.append({"id": int(post.group(1)), "time": (stamp - dt.datetime(1970, 1, 1)).total_seconds(),
                      "text": re.sub(r"\s+", " ", text).strip()})
    return sorted(posts, key=lambda p: p["id"])


def fetch(handle):
    req = urllib.request.Request("https://t.me/s/%s" % handle, headers={"User-Agent": UA})
    with xau.open_url(req, 25) as r:
        return r.read().decode("utf-8", "replace")


def announcements(name, handle, posts, state, now):
    """New lines for one channel, updating state. The first look only records where the channel is."""
    seen = state.setdefault("seen", {})
    last = state.setdefault("last", {})
    if handle not in seen:
        seen[handle] = max([p["id"] for p in posts] or [0])
        return []
    out = []
    for post in posts:
        if post["id"] <= seen[handle]:
            continue
        seen[handle] = post["id"]
        kind = classify(post["text"])
        if not kind or now - post["time"] > MAX_POST_AGE:
            continue
        key = "%s:%s" % (handle, kind)
        if key in last and now - last[key] < COOLDOWN[kind]:
            continue
        last[key] = now
        icon, verb = LABEL[kind]
        out.append("%s %s %s\nhttps://t.me/%s/%d" % (icon, name, verb, handle, post["id"]))
    return out


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


def cmd_scan():
    """Show how recent posts would be labelled, without posting anything."""
    sys.stdout.reconfigure(encoding="utf-8")
    for name, handle in CHANNELS:
        print("== %s (%s)" % (name, handle))
        for post in parse_posts(fetch(handle))[-14:]:
            print("%6d %-9s %s" % (post["id"], classify(post["text"]) or "-", post["text"][:70]))
    return 0


def cmd_run(every):
    cfg = xau.telegram_config()
    state = load_state()
    print("Watching %d channels every %ds. Ctrl+C stops it." % (len(CHANNELS), every))
    while True:
        for name, handle in CHANNELS:
            try:
                before = json.loads(json.dumps(state))
                lines = announcements(name, handle, parse_posts(fetch(handle)), state, time.time())
                try:
                    for line in lines:
                        xau.send_telegram(cfg, line)
                except Exception:
                    state.clear()
                    state.update(before)
                    raise
                save_state(state)
                print("%s: %s, %d announced." % (time.strftime("%H:%M"), handle, len(lines)))
            except (OSError, ValueError, xau.XauError) as e:
                print("%s: %s will retry (%s)." % (time.strftime("%H:%M"), handle, type(e).__name__))
        time.sleep(every)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("cmd", choices=["scan", "run"])
    ap.add_argument("--every", type=int, default=60, help="run: seconds between rounds")
    args = ap.parse_args(argv)
    try:
        return cmd_scan() if args.cmd == "scan" else cmd_run(max(30, args.every))
    except xau.XauError as e:
        print("channels.py: %s" % e, file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
