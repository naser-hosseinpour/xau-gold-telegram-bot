import calendar
import datetime as dt
import os
import types
import unittest

import xau


def bar(m, o, h, l, c, spread=0.0, day="2026-11-03"):
    return {"ts": 0, "day": day, "m": m, "o": o, "h": h, "l": l, "c": c, "spread": spread}


OR = [bar(570, 100, 101, 99, 100), bar(575, 100, 101, 99, 100), bar(580, 100, 101, 99, 100)]
ARGS = types.SimpleNamespace(balance=1000.0, max_loss=10.0, leverage=100)
TRIG = bar(585, 100, 102, 100, 101.5)


def ts(*a):
    return calendar.timegm(dt.datetime(*a).timetuple())


class Tests(unittest.TestCase):
    def test_ny_time_handles_dst(self):
        self.assertEqual(xau.ny_time(ts(2026, 11, 3, 14, 30)).hour, 9)
        self.assertEqual(xau.ny_time(ts(2026, 10, 19, 13, 30)).hour, 9)

    def test_broker_clock_to_ny(self):
        t = xau.broker_to_ny(ts(2026, 11, 3, 16, 30))
        self.assertEqual((t.hour, t.minute), (9, 30))

    def test_tick_age_on_a_live_tick(self):
        now = ts(2026, 11, 3, 17, 0, 10)
        self.assertEqual(xau.tick_age(ts(2026, 11, 3, 19, 0, 0), now), 10)

    def test_to_bars_keeps_the_cash_session(self):
        rate = lambda *a: {"time": ts(*a), "open": 1, "high": 2, "low": 0, "close": 1, "spread": 30}
        bars = xau.to_bars([rate(2026, 11, 3, 16, 25), rate(2026, 11, 3, 16, 30), rate(2026, 11, 3, 23, 0)], 0.01)
        self.assertEqual([b["m"] for b in bars], [570])
        self.assertAlmostEqual(bars[0]["spread"], 0.3)

    def test_waiting_when_no_close_above_range(self):
        self.assertEqual(xau.find_signal(OR + [bar(585, 100, 102, 100, 100.5)])["status"], "WAITING")

    def test_trigger_needs_a_close_above(self):
        s = xau.find_signal(OR + [TRIG])
        self.assertEqual((s["status"], s["mid"], s["target"]), ("TRIGGERED", 100.0, 104.5))

    def test_no_entry_after_window(self):
        self.assertEqual(xau.find_signal(OR + [bar(930, 100, 102, 100, 101.5)])["status"], "WAITING")

    def test_finished_signals(self):
        self.assertEqual(xau.find_signal(OR + [TRIG, bar(590, 101, 102, 99.5, 100)])["status"], "STOPPED")
        self.assertEqual(xau.find_signal(OR + [TRIG, bar(590, 101, 105, 101, 104)])["status"], "TARGET_HIT")

    def test_size_fits_loss_and_margin(self):
        s = xau.size(1000, 10, 3900, 3895, 100, 0.01, 200)
        self.assertEqual((s["steps"], s["lots"]), (2, 0.02))
        self.assertEqual(s["loss"], 10)
        self.assertEqual(xau.size(1000, 1000, 3900, 3899, 100, 0.01, 1)["steps"], 0)

    def test_plan_refuses_bad_trades(self):
        sig = xau.find_signal(OR + [TRIG])
        self.assertTrue(xau.plan(sig, 101, 101.5, 100, 0.01, ARGS)["ok"])
        self.assertIn("under the stop", xau.plan(sig, 99, 99.5, 100, 0.01, ARGS)["why"])
        self.assertIn("too late", xau.plan(sig, 104, 104.5, 100, 0.01, ARGS)["why"])
        tight = dict(sig, bar=bar(585, 100, 102, 100, 100.4))
        self.assertIn("too tight", xau.plan(tight, 100.1, 100.4, 100, 0.01, ARGS)["why"])
        small = types.SimpleNamespace(balance=1000.0, max_loss=1.0, leverage=100)
        self.assertIn("over your", xau.plan(sig, 101, 101.5, 100, 0.01, small)["why"])

    def test_backtest_stop_wins_a_bar_that_hits_both(self):
        bars = OR + [TRIG, bar(590, 101.5, 110, 90, 101)]
        t = xau.backtest({"d": bars}, 100, 0.01, 100)[0]
        self.assertEqual(t["outcome"], "stop")
        self.assertAlmostEqual(t["r"], -1)

    def test_backtest_pays_the_spread_on_entry(self):
        bars = OR + [TRIG, bar(590, 102, 108, 101.5, 107, spread=0.5)]
        t = xau.backtest({"d": bars}, 100, 0.01, 100)[0]
        self.assertEqual(t["outcome"], "target")
        self.assertAlmostEqual(t["r"], 2)
        self.assertEqual(t["steps"], 40)

    def test_backtest_closes_at_the_flat_time(self):
        bars = OR + [TRIG, bar(590, 102, 102.5, 101, 102), bar(950, 102, 102.5, 101, 102.2), bar(955, 90, 90, 90, 90)]
        t = xau.backtest({"d": bars}, 100, 0.01, 100)[0]
        self.assertEqual(t["outcome"], "flat")
        self.assertAlmostEqual(t["r"], 0.1)

    def test_next_message_sends_each_message_once(self):
        p = {"entry": 3950, "stop": 3945, "stop_dist": 5, "target": 3960, "target_dist": 10,
             "lots": 0.02, "loss": 10, "reward": 20}
        look = lambda status, minute=700, day="d1": {"status": status, "symbol": "DowJones.st", "day": day,
                                                     "minute": minute, "plan": p, "lines": []}
        state = {}
        self.assertIsNone(xau.next_message(look("WAITING"), state))
        first = xau.next_message(look("SIGNAL"), state)
        self.assertIn("3945.00", first)
        self.assertIn("DowJones.st", first)
        self.assertIsNone(xau.next_message(look("SIGNAL"), state))
        self.assertIn("هدف", xau.next_message(look("TARGET_HIT"), state))
        self.assertIsNone(xau.next_message(look("TARGET_HIT"), state))
        self.assertIsNotNone(xau.next_message(look("SIGNAL", day="d2"), state))

    def test_next_message_is_silent_without_a_signal(self):
        look = {"status": "STOPPED", "symbol": "X", "day": "d", "minute": 950, "plan": None, "lines": []}
        self.assertIsNone(xau.next_message(look, {}))

    def test_flat_reminder(self):
        state = {"day": "d", "signal": True}
        look = {"status": "SIGNAL", "symbol": "X", "day": "d", "minute": 950, "plan": None, "lines": []}
        self.assertIn("بستن", xau.next_message(look, state))

    def test_telegram_needs_config(self):
        saved = xau.TELEGRAM_FILE, os.environ.pop("TELEGRAM_BOT_TOKEN", None), os.environ.pop("TELEGRAM_CHAT_ID", None)
        xau.TELEGRAM_FILE = "does-not-exist.json"
        try:
            with self.assertRaises(xau.XauError):
                xau.telegram_config()
        finally:
            xau.TELEGRAM_FILE = saved[0]
            if saved[1]:
                os.environ["TELEGRAM_BOT_TOKEN"] = saved[1]
            if saved[2]:
                os.environ["TELEGRAM_CHAT_ID"] = saved[2]

    def session(self, count, close=100.0):
        out = []
        for i in range(count):
            out.append(dict(bar(570 + 5 * i, close, close + 1, close - 1, close), ts=1000 + i))
        return out

    def test_replay_sells_a_low_break(self):
        bars = self.session(20)
        bars[-1].update(o=100, l=90, c=91)
        t = xau.replay(bars)[-1]
        self.assertEqual((t["side"], t["outcome"]), (-1, "pending"))

    def test_replay_buys_then_hits_target(self):
        bars = self.session(21)
        bars[-2].update(o=100, h=112, c=111)
        bars[-1].update(o=111, h=140, l=110, c=139)
        t = xau.replay(bars)[-1]
        self.assertEqual((t["side"], t["outcome"]), (1, "target"))

    def test_replay_stop_wins_a_bar_that_hits_both(self):
        bars = self.session(21)
        bars[-2].update(o=100, h=112, c=111)
        bars[-1].update(o=111, h=200, l=50, c=100)
        self.assertEqual(xau.replay(bars)[-1]["outcome"], "stop")

    def test_replay_keeps_block_limits_on_a_quiet_day(self):
        trades = xau.replay(self.session(78), final=True)
        blocks = {}
        for t in trades:
            blocks[xau.block_of(t["m"])] = blocks.get(xau.block_of(t["m"]), 0) + 1
        self.assertEqual(sorted(blocks), [0, 1, 2])
        self.assertTrue(all(1 <= n <= xau.FAST_MAX for n in blocks.values()))
        self.assertTrue(all(t["m"] + 5 <= xau.LAST_ENTRY for t in trades))

    def test_replay_leaves_a_gap_between_signals(self):
        bars = self.session(78)
        for i, b in enumerate(bars):
            b.update(c=100 + i, h=101 + i, l=99 + i, o=100 + i)
        trades = xau.replay(bars, final=True)
        self.assertTrue(all(b["ts"] - a["ts"] >= xau.FAST_GAP for a, b in zip(trades, trades[1:])))

    def test_replay_cannot_see_the_future(self):
        bars = self.session(78)
        for i, b in enumerate(bars):
            b.update(c=100 + (i % 7), h=102 + (i % 7), l=98 - (i % 5), o=100)
        full = xau.replay(bars, final=True)
        for t in full:
            cut = [b for b in bars if b["ts"] <= t["ts"]]
            self.assertEqual(xau.replay(cut)[-1]["outcome"], "pending")
            self.assertEqual(xau.replay(cut)[-1]["side"], t["side"])

    def test_fast_plan_sell_levels(self):
        t = {"side": -1, "atr": 5.0, "m": 700}
        p = xau.fast_plan(t, 3900, 3900.3, 100, 0.01, ARGS)
        self.assertEqual((p["entry"], p["stop"], p["target"], p["lots"]), (3900, 3905, 3890, 0.02))
        self.assertEqual(p["until"], "12:45")

    def test_fast_messages_once_and_close(self):
        state = {}
        p = dict(entry=3900, stop=3905, target=3890, stop_dist=5, target_dist=10, lots=0.02, loss=10, reward=20, until="12:45")
        t = {"ts": 1, "side": -1, "outcome": "pending"}
        first = xau.fast_messages([t], "d", "XAU", lambda x: p, state)
        self.assertEqual(len(first), 1)
        self.assertIn("فروش", first[0])
        self.assertEqual(xau.fast_messages([t], "d", "XAU", lambda x: p, state), [])
        t["outcome"] = "stop"
        self.assertIn("حد ضرر", xau.fast_messages([t], "d", "XAU", lambda x: p, state)[0])
        self.assertEqual(xau.fast_messages([t], "d", "XAU", lambda x: p, state), [])

    def test_fast_messages_skip_a_missed_signal(self):
        state = {}
        t = {"ts": 1, "side": 1, "outcome": "open"}
        self.assertEqual(xau.fast_messages([t], "d", "XAU", lambda x: 1 / 0, state), [])
        t["outcome"] = "stop"
        self.assertEqual(xau.fast_messages([t], "d", "XAU", lambda x: 1 / 0, state), [])

    def test_no_order_code(self):
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "xau.py")
        with open(path, encoding="utf-8") as f:
            source = f.read()
        for word in ("order_" + "send", "order_" + "check", "log" + "in("):
            self.assertNotIn(word, source)


if __name__ == "__main__":
    unittest.main()
