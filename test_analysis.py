import calendar
import datetime as dt
import json
import random
import unittest

import analysis
import smc
import supervisor


def tehran(*a):
    return calendar.timegm((dt.datetime(*a) - analysis.TEHRAN).timetuple())


def market(seed=1, count=300, start=3900.0):
    rng = random.Random(seed)
    bars, price = [], start
    for i in range(count):
        close = price + rng.uniform(-4, 4)
        bars.append({"ts": i * 300, "h": max(price, close) + rng.uniform(0, 2),
                     "l": min(price, close) - rng.uniform(0, 2), "c": close})
        price = close
    return bars


class Tests(unittest.TestCase):
    def test_session_days_and_hours(self):
        # 2026-11-02 is a Monday
        self.assertFalse(analysis.in_session(tehran(2026, 11, 2, 15, 29)))
        self.assertTrue(analysis.in_session(tehran(2026, 11, 2, 15, 30)))
        self.assertTrue(analysis.in_session(tehran(2026, 11, 3, 0, 29)))
        self.assertFalse(analysis.in_session(tehran(2026, 11, 3, 0, 30)))
        self.assertTrue(analysis.in_session(tehran(2026, 11, 5, 0, 15)))    # Wednesday's session runs on
        self.assertTrue(analysis.in_session(tehran(2026, 11, 5, 16, 0)))    # Thursday
        self.assertTrue(analysis.in_session(tehran(2026, 11, 6, 16, 0)))    # Friday
        self.assertTrue(analysis.in_session(tehran(2026, 11, 7, 0, 15)))    # Friday runs on past midnight
        self.assertFalse(analysis.in_session(tehran(2026, 11, 7, 0, 30)))
        self.assertFalse(analysis.in_session(tehran(2026, 11, 7, 16, 0)))   # Saturday
        self.assertFalse(analysis.in_session(tehran(2026, 11, 8, 16, 0)))   # Sunday
        self.assertFalse(analysis.in_session(tehran(2026, 11, 2, 0, 15)))   # Sunday night

    def test_persian_digits(self):
        self.assertEqual(analysis.fa(3958.72, 2), "۳۹۵۸٫۷۲")
        self.assertEqual(analysis.zone_text((3955, 3957)), "۳۹۵۵–۳۹۵۷")

    def test_levels_surround_the_price_without_overlap(self):
        for seed in range(40):
            bars = market(seed)
            price = bars[-1]["c"]
            supports, resistances = analysis.find_levels(bars, bars[::3], price)
            self.assertEqual((len(supports), len(resistances)), (5, 5))
            self.assertLess(supports[0][1], price)
            self.assertGreater(resistances[0][0], price)
            for a, b in zip(supports, supports[1:]):
                self.assertGreater(a[0], b[1])
            for a, b in zip(resistances, resistances[1:]):
                self.assertLess(a[1], b[0])
            self.assertTrue(all(z[0] < z[1] for z in supports + resistances))

    def test_levels_fill_when_history_is_thin(self):
        bars = [{"ts": i, "h": 100.5, "l": 99.5, "c": 100.0} for i in range(60)]
        supports, resistances = analysis.find_levels(bars, bars, 100.0)
        self.assertEqual((len(supports), len(resistances)), (5, 5))

    def test_message_has_the_requested_parts(self):
        bars = market(3)
        made = analysis.build(bars, bars[::3], bars[-1]["c"])
        for part in ("*تحلیل XAUUSD | تایم‌فریم M5 و M15*", "قیمت مبنا", "روند کوتاه‌مدت", "تمایل یک ساعت آینده",
                     "*حمایت‌های کلیدی*", "*مقاومت‌های کلیدی*", "*سناریوهای ۵ تا ۱۵ دقیقه آینده*",
                     "سناریوی اول", "سناریوی دوم", "TP1", "TP2", "TP3", "ابطال", "*جمع‌بندی*", "سطح کلیدی فعلی"):
            self.assertIn(part, made["text"])
        self.assertLess(made["down"], bars[-1]["c"])
        self.assertGreater(made["up"], bars[-1]["c"])
        self.assertEqual(made["text"].count("*") % 2, 0)
        self.assertNotIn("_", made["text"])
        for word in ("ورود", "لات"):
            self.assertNotIn(word, made["text"])

    def test_targets_sit_beyond_the_triggers_on_every_market(self):
        for seed in range(40):
            bars = market(seed)
            made = analysis.build(bars, bars[::3], bars[-1]["c"])
            text = made["text"]
            self.assertEqual(text.count("TP1"), 2)
            lines = text.split("\n")
            for i, line in enumerate(lines):
                if line.startswith("TP"):
                    value = int(line.split(": ")[1])
                    self.assertTrue(value < made["down"] or value > made["up"], line)

    def test_timer_and_scenarios_trigger_a_new_analysis(self):
        state = {"up": 110, "down": 90, "bar_ts": 100, "next_at": 1000}
        quiet = [{"ts": 400, "h": 105, "l": 95, "c": 100}]
        self.assertEqual(analysis.why_now(500, quiet, state), (False, None))
        self.assertEqual(analysis.why_now(1000, quiet, state), (True, None))
        self.assertIn("صعودی فعال", analysis.why_now(500, [{"ts": 400, "h": 112, "l": 95, "c": 111}], state)[1])
        self.assertIn("نزولی فعال", analysis.why_now(500, [{"ts": 400, "h": 95, "l": 88, "c": 89}], state)[1])

    def test_wick_or_old_bar_does_not_trigger(self):
        state = {"up": 110, "down": 90, "bar_ts": 100, "next_at": 1000}
        self.assertEqual(analysis.why_now(500, [{"ts": 400, "h": 120, "l": 80, "c": 100}], state), (False, None))
        self.assertEqual(analysis.why_now(500, [{"ts": 100, "h": 120, "l": 80, "c": 111}], state), (False, None))

    def test_first_look_posts(self):
        self.assertEqual(analysis.why_now(500, [], {}), (True, None))

    def test_tv_gauge_matches_the_page(self):
        self.assertEqual(analysis.tv_label(-0.25), "فروش")
        self.assertEqual(analysis.tv_label(-0.5), "فروش")
        self.assertEqual(analysis.tv_label(0), "خنثی")
        self.assertEqual(analysis.tv_label(0.6), "خرید قوی")
        self.assertEqual(analysis.tv_label(None), "نامشخص")

    def test_gauges_stay_out_of_most_messages(self):
        self.assertEqual(analysis.tv_view({"Recommend.All": -0.25}, 0), {"sign": -1, "label": "فروش", "mention": True})
        self.assertFalse(analysis.tv_view({"Recommend.All": -0.25}, 1)["mention"])
        self.assertIsNone(analysis.tv_view(None, 0))
        quiet = {"sign": 1, "label": "خرید", "mention": False}
        self.assertIsNone(analysis.tv_note(quiet, 1))
        self.assertIn("هم‌راستا نیست", analysis.tv_note(quiet, -1))
        self.assertIn("تأیید", analysis.tv_note(dict(quiet, mention=True), 1))

    def test_gauges_move_the_bias(self):
        bars = market(4)
        plain = analysis.build(bars, bars[::3], bars[-1]["c"])["text"]
        pushed = analysis.build(bars, bars[::3], bars[-1]["c"], {"lines": [], "notes": [],
                                "tv": {"sign": 1, "label": "خرید", "mention": False}})["text"]
        line = lambda text: [x for x in text.split("\n") if x.startswith("تمایل")][0]
        self.assertTrue(line(plain) and line(pushed))
        self.assertIn("صعودی قوی", analysis.bias_text(4, 4050, 4030))
        self.assertIn("خنثی تا", analysis.bias_text(0, 4050, 4030))
        self.assertIn("نزولی قوی", analysis.bias_text(-5, 4050, 4030))
        self.assertIn("متمایل به نزول", analysis.bias_text(-1, 4050, 4030))
        self.assertNotIn("داده‌های TradingView", pushed)

    def test_events_show_about_four_analyses_early(self):
        now = 100000
        event = lambda minutes: {"title": "Non_Farm", "ts": now + minutes * 60, "forecast": 65, "previous": 29, "actual": None}
        self.assertEqual(analysis.event_lines([event(130)], now), ([], None))
        lines, note = analysis.event_lines([event(90)], now)
        self.assertIn("۹۰ دقیقه مانده به خبر NonFarm", lines[1])
        self.assertIn("به وقت نیویورک", lines[1])
        self.assertIsNone(note)
        self.assertIn("نزدیک", analysis.event_lines([event(20)], now)[1])
        self.assertIn("منتشر شد", analysis.event_lines([event(-10)], now)[0][1])
        self.assertEqual(analysis.event_lines([event(-40)], now), ([], None))

    def test_tv_calendar_keeps_high_impact_us_events(self):
        tv = {"result": [{"importance": 1, "country": "US", "title": "NFP", "date": "2026-11-06T13:30:00.000Z"},
                         {"importance": 0, "country": "US", "title": "Small", "date": "2026-11-06T13:30:00.000Z"},
                         {"importance": 1, "country": "EU", "title": "Euro", "date": "2026-11-06T13:30:00.000Z"}]}
        self.assertEqual([e["title"] for e in analysis.parse_tv_calendar(tv)], ["NFP"])

    def calendar_page(self):
        rows = [{"event": 8, "currency_symbol": 1, "impact": 2, "date": 3, "time": 4, "forecast": 5, "previous": 6, "actual": 7},
                "USD", "high", "2026-11-04", "18:30:00", "54.8", "54.9", None, ["Reactive", 9],
                {"event_name_fa": 10, "event_name": 11}, "شاخص  خدمات ISM", "ISM Services",
                {"event": 9, "currency_symbol": 13, "impact": 2, "date": 3, "time": 4, "forecast": 5, "previous": 6, "actual": 7}, "EUR",
                {"event": 9, "currency_symbol": 1, "impact": 15, "date": 3, "time": 4, "forecast": 5, "previous": 6, "actual": 7}, "low",
                {"event": 9, "currency_symbol": 1, "impact": 17, "date": 3, "time": 4, "forecast": 5, "previous": 6, "actual": 7}, "mid"]
        return '<script type="application/json" id="__NUXT_DATA__" data-ssr="true">%s</script>' % json.dumps(rows, ensure_ascii=False)

    def test_persian_calendar_keeps_important_dollar_events(self):
        events = analysis.parse_calendar_page(self.calendar_page())
        self.assertEqual([e["title"] for e in events], ["شاخص خدمات ISM"])
        self.assertEqual(events[0]["ts"], tehran(2026, 11, 4, 18, 30))
        self.assertEqual((events[0]["forecast"], events[0]["previous"], events[0]["actual"]), ("54.8", "54.9", None))

    def test_broken_calendar_page_raises_for_the_fallback(self):
        with self.assertRaises(AttributeError):
            analysis.parse_calendar_page("<html></html>")
        empty = '<script id="__NUXT_DATA__">[1, 2]</script>'
        with self.assertRaises(ValueError):
            analysis.parse_calendar_page(empty)

    def test_ff_feed_keeps_important_dollar_events_in_english(self):
        feed = [{"title": "Non-Farm", "country": "USD", "impact": "High", "date": "2026-11-06T08:30:00-05:00", "forecast": "65K", "previous": "29K"},
                {"title": "ISM", "country": "USD", "impact": "Medium", "date": "2026-11-06T10:00:00-05:00"},
                {"title": "Low", "country": "USD", "impact": "Low", "date": "2026-11-06T08:30:00-05:00"},
                {"title": "Euro", "country": "EUR", "impact": "High", "date": "2026-11-06T08:30:00-05:00"}]
        events = analysis.parse_ff_feed(feed)
        self.assertEqual([e["title"] for e in events], ["Non-Farm"])
        self.assertEqual(events[0]["ts"], tehran(2026, 11, 6, 17, 0))

    def run_sources(self, working):
        calls = []

        def text(url):
            calls.append("live")
            if "live" in working:
                return self.calendar_page()
            raise OSError("down")

        def get(url, headers=None):
            name = "forexfactory" if url == analysis.FF_FEED else "tradingview" if "economic-calendar" in url else "other"
            calls.append(name)
            if name in working:
                return [] if name == "forexfactory" else {"result": []}
            raise OSError("down")

        saved = analysis.fetch_text, analysis._get_json
        analysis.fetch_text, analysis._get_json = text, get
        try:
            return analysis.fetch_events(1000), calls
        finally:
            analysis.fetch_text, analysis._get_json = saved

    def test_news_source_order(self):
        (events, source), calls = self.run_sources({"live", "forexfactory", "tradingview"})
        self.assertEqual((source, calls), ("live", ["live"]))
        self.assertEqual(events[0]["title"], "شاخص خدمات ISM")
        self.assertEqual(self.run_sources({"forexfactory", "tradingview"}), (([], "forexfactory"), ["live", "forexfactory"]))
        self.assertEqual(self.run_sources({"tradingview"}), (([], "tradingview"), ["live", "forexfactory", "tradingview"]))
        self.assertEqual(self.run_sources(set())[0], ([], None))

    def test_tv_headlines_only_when_both_forex_factory_sources_fail(self):
        saved = analysis.fetch_events, analysis._get_json, analysis.parse_gld, analysis.fetch_tradingview
        now = 100000
        analysis._get_json = lambda url, headers=None: {"items": [{"id": "a", "title": "Gold up", "source": "Reuters", "published": now}]}
        analysis.parse_gld = lambda data, now: []
        analysis.fetch_tradingview = lambda: None
        try:
            for source, expected in (("live", None), ("forexfactory", None), ("tradingview", "a"), (None, "a")):
                analysis.fetch_events = lambda n: ([], source)
                self.assertEqual(analysis.gather_extra(now, {})["headline"], expected)
        finally:
            analysis.fetch_events, analysis._get_json, analysis.parse_gld, analysis.fetch_tradingview = saved

    def test_news_time_is_new_york(self):
        ts = tehran(2026, 11, 6, 17, 0)
        self.assertEqual(analysis.new_york_text(ts), "جمعه ساعت ۰۸:۳۰ به وقت نیویورک")
        self.assertIn("۲۷ دقیقه مانده به خبر NFP", analysis.event_lines(
            [{"title": "NFP", "ts": ts, "forecast": "65K", "previous": "29K", "actual": None}], ts - 27 * 60)[0][1])
        self.assertIn("۱ دقیقه مانده", analysis.event_lines(
            [{"title": "NFP", "ts": ts, "forecast": "65K", "previous": "29K", "actual": None}], ts - 20)[0][1])

    def test_empty_forecast_reads_as_unknown(self):
        event = {"title": "x", "ts": 100600, "forecast": "", "previous": None, "actual": ""}
        self.assertIn("پیش‌بینی نامشخص، قبلی نامشخص", analysis.event_lines([event], 100000)[0][1])

    def test_headline_choice(self):
        now = 100000
        item = lambda i, title, source="Reuters", age=600: {"id": i, "title": title, "source": source, "published": now - age}
        items = [item("a", "Gold heads for biggest gain"), item("b", "Gold exploration program", age=60),
                 item("c", "Gold rally", "Beincrypto", age=30), item("d", "Stocks rise", age=10),
                 item("e", "Gold falls", age=99999)]
        self.assertEqual(analysis.pick_headline(items, now, [])["id"], "a")
        self.assertIsNone(analysis.pick_headline(items, now, ["a"]))
        self.assertEqual(analysis.headline_lines(None), [])
        self.assertIn("Reuters", analysis.headline_lines(items[0])[1])

    def test_day_trader_block(self):
        bars = [{"m": 570 + i * 5, "h": 361, "l": 360, "c": 360.5} for i in range(3)]
        self.assertIn("داخل محدوده", analysis.day_trader_lines(bars + [{"m": 585, "h": 361, "l": 360, "c": 360.5}])[1])
        self.assertIn("بالای سقف", analysis.day_trader_lines(bars + [{"m": 585, "h": 362, "l": 360, "c": 361.5}])[1])
        self.assertIn("زیر کف", analysis.day_trader_lines(bars + [{"m": 585, "h": 361, "l": 359, "c": 359.5}])[1])
        self.assertEqual(analysis.day_trader_lines(bars[:2]), [])

    def test_gld_bars_keep_only_closed_bars_of_today(self):
        now = tehran(2026, 11, 4, 19, 0)
        data = {"chart": {"result": [{"timestamp": [now - 86400, now - 600, now - 100],
                "indicators": {"quote": [{"high": [1, 2, 3], "low": [1, 2, 3], "close": [1, 2, 3]}]}}]}}
        self.assertEqual([b["c"] for b in analysis.parse_gld(data, now)], [2])

    def test_extra_goes_into_the_message_and_stays_markdown_safe(self):
        bars = market(4)
        extra = {"lines": ["*اخبار فارکس فکتوری*", "• Test", ""], "notes": ["یادداشت"]}
        text = analysis.build(bars, bars[::3], bars[-1]["c"], extra)["text"]
        self.assertLess(text.index("اخبار فارکس"), text.index("حمایت‌های کلیدی"))
        self.assertGreater(text.index("یادداشت"), text.index("جمع‌بندی"))
        self.assertEqual(text.count("*") % 2, 0)

    def test_session_levels(self):
        ts = lambda *a: calendar.timegm(dt.datetime(*a).timetuple())
        bar = lambda t, h, l: {"ts": t, "h": h, "l": l, "c": l}
        bars = [bar(ts(2026, 11, 3, 20, 0), 120, 90), bar(ts(2026, 11, 3, 21, 0), 130, 95),
                bar(ts(2026, 11, 4, 3, 0), 110, 100), bar(ts(2026, 11, 4, 9, 0), 115, 105),
                bar(ts(2026, 11, 4, 11, 0), 125, 112), bar(ts(2026, 11, 4, 16, 0), 200, 50)]
        got = smc.session_levels(bars, ts(2026, 11, 4, 16, 30))
        self.assertEqual(got, {"yesterday": (130, 90), "asia": (115, 100), "london": (125, 112)})
        self.assertIsNone(smc.session_levels(bars[:2], ts(2026, 11, 4, 1, 0))["asia"])

    def test_round_levels(self):
        self.assertEqual(smc.round_levels(4038.7), (4000, 4050))
        self.assertEqual(smc.round_levels(4050), (4000, 4100))

    def zigzag(self, points):
        bars = []
        for a, b in zip(points, points[1:]):
            for i in range(10):
                c = a + (b - a) * (i + 1) / 10.0
                bars.append({"ts": len(bars) * 300, "h": c + 0.2, "l": c - 0.2, "c": c})
        return bars

    def test_structure_bos_and_choch(self):
        up = smc.market_structure(self.zigzag([100, 110, 105, 115, 110, 120]))
        self.assertEqual((up["trend"], up["last"]["kind"], up["last"]["side"]), (1, "BOS", 1))
        turned = smc.market_structure(self.zigzag([100, 110, 105, 115, 110, 120, 112, 118, 100]))
        self.assertEqual((turned["trend"], turned["last"]["kind"], turned["last"]["side"]), (-1, "CHoCH", -1))
        self.assertIsNone(smc.market_structure(self.zigzag([100, 101]))["last"])

    def test_fair_value_gaps(self):
        b = lambda h, l, c: {"ts": 0, "h": h, "l": l, "c": c}
        bull = [b(100, 98, 99), b(104, 99, 103), b(106, 102, 105)]
        gaps = smc.fair_value_gaps(bull)
        self.assertEqual([(g["side"], g["low"], g["high"]) for g in gaps], [(1, 100, 102)])
        self.assertEqual(smc.fair_value_gaps(bull + [b(105, 99, 99.5)]), [])
        self.assertEqual(len(smc.fair_value_gaps(bull + [b(105, 100.5, 101)])), 1)
        bear = [b(106, 104, 105), b(105, 100, 101), b(102, 99, 100)]
        self.assertEqual([(g["side"], g["low"], g["high"]) for g in smc.fair_value_gaps(bear)], [(-1, 102, 104)])
        self.assertEqual(len(smc.fair_value_gaps(bear + [b(103, 100, 102)])), 1)
        self.assertEqual(smc.fair_value_gaps(bear + [b(105, 100, 104.5)]), [])
        low, high = smc.nearest_gaps([{"side": 1, "low": 90, "high": 92}, {"side": 1, "low": 95, "high": 97},
                                      {"side": -1, "low": 110, "high": 112}, {"side": -1, "low": 105, "high": 107}], 100)
        self.assertEqual((low["high"], high["low"]), (97, 105))

    def test_fibonacci(self):
        up = smc.fibonacci(self.zigzag([100, 120, 105, 125, 115]))
        self.assertIsNone(smc.fibonacci(self.zigzag([100, 101])))
        self.assertEqual(up["side"], 1)
        spot = dict(up["levels"])
        self.assertAlmostEqual(spot[0.5], (up["start"] + up["end"]) / 2)
        self.assertGreater(spot[0.382], spot[0.618])
        down = smc.fibonacci(self.zigzag([120, 100, 115, 95, 105]))
        self.assertEqual(down["side"], -1)
        self.assertLess(dict(down["levels"])[0.382], dict(down["levels"])[0.618])

    def test_message_has_the_new_sections(self):
        bars = market(5)
        text = analysis.build(bars, bars[::3], bars[-1]["c"])["text"]
        for part in ("*جمع‌بندی اندیکاتورها*", "*ساختار و نواحی واکنش*", "*سطح‌های مهم*", "اعداد رند", "*حمایت‌های کلیدی*"):
            self.assertIn(part, text)
        self.assertNotIn("۰٫۳۸۲", text)
        self.assertNotIn("۰٫۶۱۸", text)
        self.assertEqual(text.count("*") % 2, 0)
        self.assertLess(len(text), 4096)

    def test_reaction_areas(self):
        bull = {"low": 90, "high": 92}
        bear = {"low": 110, "high": 112}
        areas = smc.reaction_areas(bull, bear, [91, 130], 100, 1)
        self.assertEqual([(a["price"], a["gap"], a["fib"]) for a in areas], [(91, True, True), (111, True, False)])
        alone = smc.reaction_areas(None, None, [95, 80, 104], 100, 1)
        self.assertEqual([(a["price"], a["gap"], a["fib"]) for a in alone], [(104, False, True), (95, False, True)])
        self.assertEqual(smc.reaction_areas(None, None, [], 100, 1), [])

    def test_area_sentences_stay_simple(self):
        both = analysis.area_line({"price": 4040.4, "gap": True, "fib": True})
        self.assertIn("حوالی ۴۰۴۰", both)
        self.assertIn("فیبوناچی هم همین ناحیه", both)
        self.assertIn("احتمال زیادی", both)
        self.assertNotIn("٫", both)
        self.assertNotIn("احتمال زیادی", analysis.area_line({"price": 4040, "gap": True, "fib": True}, True))
        self.assertIn("ممکن است", analysis.area_line({"price": 4040, "gap": True, "fib": False}))
        self.assertIn("فیبوناچی ناحیه", analysis.area_line({"price": 4040, "gap": False, "fib": True}))

    def test_gap_and_fibonacci_agreement(self):
        fib = lambda side, level: {"side": side, "levels": [(0.5, level)]}
        bull = {"low": 90, "high": 92}
        bear = {"low": 110, "high": 112}
        self.assertEqual(smc.agreement(bull, None, fib(1, 91), 1), {"state": "converge", "side": 1})
        self.assertEqual(smc.agreement(None, bear, fib(-1, 111), 1), {"state": "converge", "side": -1})
        self.assertEqual(smc.agreement(bull, None, fib(-1, 91), 1)["state"], "diverge")
        self.assertEqual(smc.agreement(None, bear, fib(1, 111), 1)["state"], "diverge")
        self.assertEqual(smc.agreement(bull, bear, fib(1, 100), 1)["state"], "apart")
        self.assertIsNone(smc.agreement(None, None, fib(1, 100), 1))
        self.assertIsNone(smc.agreement(bull, None, None, 1))

    def test_agreement_sentences(self):
        self.assertIsNone(analysis.agreement_line(None))
        self.assertIn("همگرایی صعودی", analysis.agreement_line({"state": "converge", "side": 1}))
        self.assertIn("همگرایی نزولی", analysis.agreement_line({"state": "converge", "side": -1}))
        self.assertIn("واگرایی", analysis.agreement_line({"state": "diverge", "side": 1}))
        self.assertIn("همگرایی مشخصی نیست", analysis.agreement_line({"state": "apart", "side": 0}))

    def test_gold_command_is_the_whole_message(self):
        for text in ("gold", "GOLD", " Gold ", "/gold", "/gold@ExampleBot", "گلد"):
            self.assertTrue(analysis.is_gold_command(text), text)
        for text in ("gold is up", "i like gold", "طلا", "", None):
            self.assertFalse(analysis.is_gold_command(text), text)

    def test_request_needs_the_group_and_a_fresh_message(self):
        message = lambda chat, text, date: {"update_id": 1, "message": {"chat": {"id": chat}, "text": text, "date": date}}
        self.assertTrue(analysis.requested([message(-5, "gold", 1000)], "-5", 1010))
        self.assertFalse(analysis.requested([message(-9, "gold", 1000)], "-5", 1010))
        self.assertFalse(analysis.requested([message(-5, "hello", 1000)], "-5", 1010))
        self.assertFalse(analysis.requested([message(-5, "gold", 1000)], "-5", 1000 + analysis.COMMAND_MAX_AGE + 1))
        self.assertFalse(analysis.requested([{"update_id": 2}], "-5", 1010))
        self.assertEqual(analysis.next_offset([{"update_id": 7}, {"update_id": 9}], None), 10)
        self.assertEqual(analysis.next_offset([], 5), 5)

    def test_request_is_answered_once_a_minute(self):
        saved = analysis.read_updates, analysis.answer_request, analysis.xau.send_telegram
        calls = []
        notes = []
        analysis.xau.send_telegram = lambda cfg, text, parse_mode=None: notes.append(text)
        update = {"update_id": 1, "message": {"chat": {"id": -5}, "text": "gold", "date": 1000}}
        analysis.read_updates = lambda cfg, offset: [update]
        analysis.answer_request = lambda term, cfg, state, now: calls.append(now)
        try:
            marks = {}
            cfg = {"chat_id": "-5"}
            analysis.check_requests(None, cfg, {}, marks, 1000)
            analysis.check_requests(None, cfg, {}, marks, 1010)
            analysis.check_requests(None, cfg, {}, marks, 1020)
            self.assertEqual(calls, [1000])
            self.assertEqual(len(notes), 1)
            self.assertIn("۵۰ ثانیه", notes[0])
            self.assertEqual(marks["offset"], 2)
            update["message"]["date"] = 1060
            analysis.check_requests(None, cfg, {}, marks, 1060)
            self.assertEqual(calls, [1000, 1060])
        finally:
            analysis.read_updates, analysis.answer_request, analysis.xau.send_telegram = saved

    def test_each_request_reads_the_current_proxy(self):
        import xau
        seen = []

        class Opener:
            def open(self, request, timeout):
                return "response"

        saved = xau.urllib.request.getproxies, xau.urllib.request.build_opener, xau.urllib.request.ProxyHandler
        ports = iter(("1", "2"))
        xau.urllib.request.getproxies = lambda: {"https": next(ports)}
        xau.urllib.request.ProxyHandler = lambda proxies: seen.append(proxies)
        xau.urllib.request.build_opener = lambda handler: Opener()
        try:
            self.assertEqual(xau.open_url("r", 5), "response")
            xau.open_url("r", 5)
        finally:
            xau.urllib.request.getproxies, xau.urllib.request.build_opener, xau.urllib.request.ProxyHandler = saved
        self.assertEqual(seen, [{"https": "1"}, {"https": "2"}])

    def test_portable_mt5_is_used_when_named(self):
        import os as _os
        import xau
        calls = []

        class Fake:
            def initialize(self, **kw):
                calls.append(kw)
                return True

        saved = _os.environ.pop("TRADER_MT5", None)
        try:
            xau.connect(Fake())
            _os.environ["TRADER_MT5"] = r"C:\x\terminal64.exe"
            xau.connect(Fake())
        finally:
            _os.environ.pop("TRADER_MT5", None)
            if saved:
                _os.environ["TRADER_MT5"] = saved
        self.assertEqual(calls[0], {"timeout": 30000})
        self.assertEqual(calls[1], {"path": r"C:\x\terminal64.exe", "portable": True, "timeout": 30000})

    def test_group_upgrade_is_detected(self):
        import xau
        body = json.dumps({"ok": False, "parameters": {"migrate_to_chat_id": -100123}})
        self.assertEqual(xau.migrated_chat(body), -100123)
        self.assertIsNone(xau.migrated_chat(json.dumps({"ok": False})))
        self.assertIsNone(xau.migrated_chat("not json"))

    def test_rsi(self):
        self.assertIsNone(smc.rsi([1, 2, 3]))
        self.assertEqual(smc.rsi(list(range(1, 40))), 100.0)
        self.assertAlmostEqual(smc.rsi(list(range(40, 1, -1))), 0.0)
        flip = [100 + (i % 2) for i in range(60)]
        self.assertAlmostEqual(smc.rsi(flip), 50, delta=5)
        self.assertEqual([smc.rsi_vote(v) for v in (60, 50, 40, None)], [1, 0, -1, 0])

    def test_moving_average_vote(self):
        self.assertEqual(smc.ma_vote([float(i) for i in range(100)]), 1)
        self.assertEqual(smc.ma_vote([float(100 - i) for i in range(100)]), -1)
        self.assertEqual(smc.ma_vote([100.0] * 100), 0)

    def test_price_action_and_trend_line_votes(self):
        up = self.zigzag([100, 110, 105, 115, 110, 120, 118])
        down = self.zigzag([120, 110, 115, 105, 110, 100, 102])
        mixed = self.zigzag([100, 120, 105, 115, 95, 118, 100])
        self.assertEqual(smc.price_action_vote(up), 1)
        self.assertEqual(smc.price_action_vote(down), -1)
        self.assertEqual(smc.price_action_vote(mixed), 0)
        self.assertEqual(smc.trend_line_vote(up), 1)
        self.assertEqual(smc.trend_line_vote(down), -1)
        broken = self.zigzag([100, 110, 105, 115, 110, 120, 100])
        self.assertEqual(smc.trend_line_vote(broken), 0)
        self.assertEqual(smc.price_action_vote(self.zigzag([100, 101])), 0)

    def test_trend_verdict_has_five_states(self):
        got = [smc.trend_verdict(x) for x in (6, 4, 3, 2, 1, 0, -1, -2, -3, -4, -6)]
        self.assertEqual(got, ["strong_up", "strong_up", "up", "up", "none", "none", "none",
                               "down", "down", "strong_down", "strong_down"])

    def test_indicator_block(self):
        rising = [{"ts": i * 300, "h": 100 + i + 0.5, "l": 100 + i - 0.5, "c": 100.0 + i} for i in range(120)]
        lines = analysis.indicator_lines(rising, {"sign": 1, "label": "خرید", "mention": False})
        self.assertEqual(lines[0], "*جمع‌بندی اندیکاتورها*")
        self.assertIn("با توجه به اندیکاتورهایی که ربات", lines[1])
        self.assertIn("اشباع خرید", lines[2])
        self.assertIn("میانگین متحرک: صعودی", lines[2])
        for verdict in ("strong_up", "up", "none", "down", "strong_down"):
            self.assertIn(verdict, analysis.VERDICTS)

    def test_pause_window_around_red_news(self):
        event = {"title": "Non_Farm", "ts": 100000}
        pause = lambda seconds: analysis.pause_event([event], 100000 - seconds)
        self.assertIsNone(pause(analysis.PAUSE_BEFORE + 1))
        self.assertIs(pause(analysis.PAUSE_BEFORE), event)
        self.assertIs(pause(-analysis.PAUSE_AFTER), event)
        self.assertIsNone(pause(-analysis.PAUSE_AFTER - 1))
        before = analysis.pause_text(event, 100000 - 420)
        self.assertIn("۷ دقیقه مانده به خبر قرمز NonFarm", before)
        self.assertIn("منتظر انتشار خبر بمانید", before)
        self.assertNotIn("TP1", before)
        self.assertIn("منتشر شد", analysis.pause_text(event, 100060))
        self.assertEqual(before.count("*") % 2, 0)

    def test_outage_alerts_once(self):
        health = {}
        self.assertIsNone(analysis.track_health(health, False))
        self.assertIsNone(analysis.track_health(health, False))
        self.assertIn("قطع", analysis.track_health(health, False))
        self.assertIsNone(analysis.track_health(health, False))
        self.assertIn("برگشت", analysis.track_health(health, True))
        self.assertIsNone(analysis.track_health(health, True))

    def test_supervisor_messages(self):
        self.assertIsNone(supervisor.outage_message(None, 10000))
        self.assertIsNone(supervisor.outage_message(9900, 10000))
        self.assertIn("۱۰".translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789")), supervisor.outage_message(9400, 10000))
        self.assertTrue(supervisor.due_for_restart(0, supervisor.RESTART_WAIT))
        self.assertFalse(supervisor.due_for_restart(0, supervisor.RESTART_WAIT - 1))

    def test_no_order_code(self):
        with open(analysis.__file__, encoding="utf-8") as f:
            self.assertNotIn("order_" + "send", f.read())


if __name__ == "__main__":
    unittest.main()
