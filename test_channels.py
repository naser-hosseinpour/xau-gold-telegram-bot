import json
import unittest

import channels

PAGE = ('<div class="tgme_widget_message_wrap"><div data-post="ch/10">'
        '<div class="tgme_widget_message_text js">لایو ترید &amp; <a href="https://youtube.com/live/x">لینک</a></div>'
        '<time datetime="2026-11-04T10:00:00+00:00"></time></div>'
        '<div class="tgme_widget_message_wrap"><div data-post="ch/11"><div class="photo"></div>'
        '<time datetime="2026-11-04T11:00:00+00:00"></time></div>')


def post(i, text, when=1000):
    return {"id": i, "time": when, "text": text}


class Tests(unittest.TestCase):
    def test_classify_real_styles(self):
        for text, kind in (("لایو ترید سشن نیویورک چهارشنبه", "live"),
                           ("هم اکنون روی یوتیوب لایو هستیم", "live"),
                           ("https://meet.google.com/abc", "live"),
                           ("پوزیشن منتورینگ", "signal"),
                           ("ورود بعدی منتورینگ", "signal"),
                           ("BUY 4030 TP1 4040 SL 4020", "signal"),
                           ("تحلیل 13 آبان طلا منتشر شد", "analysis"),
                           ("اولین زون حمایتی کوتاه‌مدت رو ۴۰۳۲-۴۰۳۵ در نظر دارم", "analysis"),
                           ("آپدیت طلا مهم", "analysis")):
            self.assertEqual(channels.classify(text), kind, text)

    def test_classify_leaves_out_noise(self):
        for text in ("پوزیشن منتورینگ ۱۵۰ پیپ", "ممنون استاد عزیز", "مراقب کانال فیک باشین",
                     "تقویم اقتصادی امروز", "معرفی صرافی Toobit", "درود دوستان روزتون به خیر", ""):
            self.assertIsNone(channels.classify(text), text)

    def test_reports_about_a_past_live_are_not_a_live(self):
        for text in ("شات سود رو ارسال کردم براتون، با لایو شما تازه ضررهای امروزم صفر و جبران شد تشکر از شما",
                     "پوزیشن اخر لایو دادم،برررکت",
                     "درود بر شما پوزیشن آخر لایو که استاد دادن برکت شد",
                     "این جمله رو در لایو یوتوب بهتون گفتم"):
            self.assertNotEqual(channels.classify(text), "live", text)

    def test_real_live_notices_still_count(self):
        for text in ("دوستان تا نیم ساعت دیگه لایو ترید شروع می‌کنیم",
                     "هم اکنون روی یوتیوب لایو هستیم",
                     "امشب قبل از باز شدن مارکت لایو\u200c خواهیم داشت",
                     "https://youtube.com/live/abc امروز ساعت ۱۷.۴۵"):
            self.assertEqual(channels.classify(text), "live", text)

    def test_stop_loss_talk_is_not_skipped(self):
        self.assertEqual(channels.classify("پوزیشن جدید حد ضرر 4030"), "signal")

    def test_past_live_is_not_a_live(self):
        self.assertEqual(channels.classify("از زون دوم خرید داد من لایو بودم"), "analysis")

    def test_parse_page(self):
        posts = channels.parse_posts(PAGE)
        self.assertEqual([p["id"] for p in posts], [10, 11])
        self.assertIn("youtube.com/live", posts[0]["text"])
        self.assertIn("&", posts[0]["text"])
        self.assertEqual(posts[1]["text"], "")

    def test_first_look_stays_quiet(self):
        state = {}
        self.assertEqual(channels.announcements("تحلیلگر", "ch", [post(5, "لایو")], state, 1000), [])
        self.assertEqual(state["seen"]["ch"], 5)

    def test_new_post_is_announced_once_with_name_and_link(self):
        state = {"seen": {"ch": 5}}
        lines = channels.announcements("تحلیلگر", "ch", [post(5, "لایو"), post(6, "لایو ترید")], state, 1000)
        self.assertEqual(lines, ["🔴 تحلیلگر لایو گذاشته\nhttps://t.me/ch/6"])
        self.assertEqual(channels.announcements("تحلیلگر", "ch", [post(6, "لایو ترید")], state, 1000), [])

    def test_cooldown_groups_repeats(self):
        state = {"seen": {"ch": 1}}
        self.assertEqual(len(channels.announcements("ن", "ch", [post(2, "لایو ترید")], state, 1000)), 1)
        self.assertEqual(channels.announcements("ن", "ch", [post(3, "لایو ترید")], state, 1000 + 60), [])
        later = 1000 + channels.COOLDOWN["live"]
        self.assertEqual(len(channels.announcements("ن", "ch", [post(4, "لایو ترید", later)], state, later)), 1)

    def test_signals_are_never_held_back(self):
        state = {"seen": {"ch": 1}}
        lines = channels.announcements("ن", "ch", [post(2, "پوزیشن منتورینگ"), post(3, "ورود بعدی")], state, 1000)
        self.assertEqual(len(lines), 2)

    def test_old_posts_stay_silent(self):
        state = {"seen": {"ch": 1}}
        old = channels.MAX_POST_AGE + 1
        self.assertEqual(channels.announcements("ن", "ch", [post(2, "لایو", 0)], state, old), [])
        self.assertEqual(state["seen"]["ch"], 2)

    def test_channels_load_from_file(self):
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
            json.dump([["Analyst", "somechannel"]], f)
        self.assertEqual(channels.load_channels(f.name), [("Analyst", "somechannel")])
        self.assertEqual(channels.load_channels(f.name + ".missing"), [])

if __name__ == "__main__":
    unittest.main()
