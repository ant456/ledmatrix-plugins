"""Offline tests for News Arcade (run: python -m pytest test_news_arcade.py, or python test_news_arcade.py).

The LEDMatrix base class is stubbed, so nothing here touches a panel or the network.
"""
import importlib
import logging
import os
import sys
import types
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
FONTS = os.environ.get("ARCADE_FONT_DIR") or os.path.join(os.path.dirname(HERE), "fonts")
if os.path.isdir(FONTS):
    os.environ["ARCADE_FONT_DIR"] = FONTS


class BasePlugin:
    def __init__(self, plugin_id, config, display_manager, cache_manager, plugin_manager):
        self.plugin_id, self.config, self.display_manager = plugin_id, config, display_manager
        self.logger = logging.getLogger("news-arcade-test")

    @property
    def layout(self):                      # the real BasePlugin has a read-only layout property and a style() method
        return None

    def style(self, element_key, classic_font=None):
        return None

    def on_config_change(self, c):
        pass

    def get_info(self):
        return {}

    def cleanup(self):
        pass


for _name in ("src", "src.plugin_system"):
    sys.modules.setdefault(_name, types.ModuleType(_name))
_base = types.ModuleType("src.plugin_system.base_plugin")
_base.BasePlugin = BasePlugin
sys.modules["src.plugin_system.base_plugin"] = _base

import manager as mgr          # noqa: E402
import newsarc_render as render  # noqa: E402
import newsarc_feeds as feeds    # noqa: E402


class DM:
    width, height = 128, 32


LOCAL = ["15-year-old shot and killed in Port Richmond on Halloween night",
         "Philly police officer struck by car for 2nd day in a row",
         "Live updates: Sixers come back in final minutes to beat Hornets on Halloween"]
SPORT = ["WNBA Finals Game 7 live updates, analysis and highlights",
         "Sources: Colts willing to trade Taylor, active in talks with multiple teams"]


def make(cfg=None, local=LOCAL, sport=SPORT):
    c = {"feeds": [{"name": "LOCAL NEWS", "url": "x"}, {"name": "TOP SPORTS", "url": "x"}]}
    c.update(cfg or {})
    p = mgr.NewsArcadePlugin("news-arcade", c, DM(), None, None)
    p.root = __import__("pathlib").Path(HERE) / "no-such-folder"

    def items(feed, titles):
        return [{"feed": feed, "title": t, "desc": "", "link": t, "ts": 1000.0} for t in titles]
    p._by_feed = {"NBC10": items("NBC10", local), "TOP SPORTS": items("TOP SPORTS", sport)}
    p._rebuild(1000.0)
    return p


def lit(img):
    """Columns that hold any lit pixel in the text area (right of the tile)."""
    return {x for x in range(img.width) for y in range(img.height) if sum(img.getpixel((x, y))) > 0}


class Ticker(unittest.TestCase):
    def test_every_story_is_on_one_strip_round_robin(self):
        p = make()
        s = p._strip
        self.assertEqual(len(s.items), 5)
        self.assertEqual([i["feed"] for i in s.items], ["NBC10", "TOP SPORTS", "NBC10", "TOP SPORTS", "NBC10"])
        self.assertEqual(s.spans[0][0], 0)
        self.assertEqual(s.spans[-1][1], s.total)
        for (a, b), (c, d) in zip(s.spans, s.spans[1:]):
            self.assertEqual(b, c)                     # no gaps or overlaps

    def test_a_long_headline_is_drawn_in_full_not_cut_off(self):
        p = make(local=["A" * 60], sport=["B" * 5])
        s = p._strip
        a, b = s.spans[0]
        text_w = render.font.text_width("A" * 60, render.TITLE)
        self.assertGreater(text_w, p._view_w())        # wider than the screen, as real headlines are
        self.assertEqual(b - a, render.BAR + render.PAD + text_w + render.GAP)

    def test_the_lap_has_no_seam(self):
        p = make()
        s = p._strip
        for x in (0, 1, s.total - s.view_w // 2, s.total - 1):
            self.assertEqual(s.window(x).size, (s.view_w, 32))
        a = s.window(s.total - 5).crop((5, 0, s.view_w, 32))
        b = s.window(0).crop((0, 0, s.view_w - 5, 32))
        self.assertEqual(a.tobytes(), b.tobytes())     # the wrap repeats the start exactly

    def test_a_short_list_repeats_so_the_window_is_never_empty(self):
        p = make(local=["HI"], sport=[])
        s = p._strip
        self.assertGreaterEqual(s.total, s.view_w)
        self.assertEqual(s.window(0).size, (s.view_w, 32))

    def test_it_scrolls_at_the_configured_speed_and_wraps(self):
        p = make({"crawl_speed": 40})
        p.compose(1000.0)
        p.compose(1000.4)
        self.assertAlmostEqual(p._scroll, 16.0, places=3)
        p._scroll = p._strip.total - 4
        p._scroll_t = 2000.0
        p.compose(2000.5)                              # +20px wraps past the end of the lap
        self.assertAlmostEqual(p._scroll, 16.0, places=3)

    def test_a_long_gap_resumes_instead_of_jumping(self):
        p = make({"crawl_speed": 40})
        p.compose(1000.0)
        p.compose(1000.5)
        before = p._scroll
        p.compose(1500.0)                              # other plugins had the panel for minutes
        self.assertEqual(p._scroll, before)

    def test_the_tile_follows_the_story_at_the_left_edge(self):
        p = make()
        s = p._strip
        for i in (0, 1):
            p._scroll = float(s.spans[i][0]) + 3
            p._scroll_t = 1000.0
            frame = p.compose(1000.0)
            tile = p._tile(s.items[i])
            self.assertEqual(frame.crop((0, 0, render.TILE_W, 32)).tobytes(), tile.tobytes())
        t0 = p._tile(s.items[0]).tobytes()
        t1 = p._tile(s.items[1]).tobytes()
        self.assertNotEqual(t0, t1)

    def test_without_logos_the_strip_uses_the_whole_width(self):
        p = make({"show_logos": False})
        self.assertEqual(p._strip.view_w, 128)
        self.assertEqual(p.compose(1000.0).size, (128, 32))

    def test_frames_are_always_panel_sized_and_have_text(self):
        p = make()
        t = 1000.0
        for _ in range(300):
            t += 0.1
            f = p.compose(t)
            self.assertEqual(f.size, (128, 32))
        self.assertTrue(lit(f))

    def test_a_new_story_keeps_the_place_in_the_ticker(self):
        p = make()
        s = p._strip
        p._scroll, p._scroll_t = float(s.spans[3][0]) + 10, 1000.0
        title = s.items[3]["title"]
        p._by_feed["NBC10"].insert(0, {"feed": "NBC10", "title": "A brand new first story", "desc": "", "link": "n", "ts": 1000.0})
        p._rebuild(1001.0)
        new = p._strip
        self.assertEqual(new.items[new.index_at(p._scroll)]["title"], title)

    def test_no_feeds_and_no_headlines(self):
        p = make(local=[], sport=[])
        self.assertEqual(p._strip.total, 0)
        self.assertEqual(p.compose(1000.0).size, (128, 32))     # the NO NEWS screen, no crash

    def test_cards_layout_is_still_available(self):
        p = make({"layout": "cards"})
        self.assertEqual(p.view_mode, "cards")
        self.assertEqual(p.compose(1000.0).size, (128, 32))

    def test_default_layout_is_ticker_and_junk_falls_back(self):
        self.assertEqual(make().view_mode, "ticker")
        self.assertEqual(make({"layout": "banana"}).view_mode, "ticker")


class Breaking(unittest.TestCase):
    def alert(self, p, title):
        return {"item": {"feed": "NBC10", "title": title, "desc": "", "link": title, "ts": 1000.0},
                "color": (200, 30, 30), "logo": "", "t0": 0.0, "pending": True}

    def test_a_long_breaking_headline_stays_up_long_enough_to_read(self):
        p = make()
        long = "BREAKING: " + "word " * 25
        p._alert = self.alert(p, long)
        p.compose(1000.0)
        self.assertGreater(p._alert["dur"], mgr.BURST_SECONDS)
        self.assertLessEqual(p._alert["dur"], mgr.MAX_BURST_SECONDS)

    def test_a_short_one_keeps_the_normal_burst_length(self):
        p = make()
        p._alert = self.alert(p, "Short")
        p.compose(1000.0)
        self.assertEqual(p._alert["dur"], mgr.BURST_SECONDS)

    def test_after_the_burst_the_story_is_at_the_left_of_the_ticker(self):
        p = make()
        title = LOCAL[2]
        p._alert = self.alert(p, title)
        p.compose(1000.0)
        p.compose(1000.0 + p._alert["dur"] + 0.1)      # burst over
        self.assertIsNone(p._alert)
        s = p._strip
        self.assertEqual(s.items[s.index_at(p._scroll)]["title"], title)


class Parsing(unittest.TestCase):
    def test_rss_and_atom_still_parse(self):
        rss = b"<rss><channel><item><title>One &amp; two</title><link>http://a</link></item></channel></rss>"
        self.assertEqual(feeds.parse_feed(rss, "F", 5)[0]["title"], "One & two")
        atom = (b'<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>Hello</title>'
                b'<link href="http://b"/></entry></feed>')
        self.assertEqual(feeds.parse_feed(atom, "F", 5)[0]["link"], "http://b")


if __name__ == "__main__":
    unittest.main()
