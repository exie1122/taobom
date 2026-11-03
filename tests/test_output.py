import json
import unittest

from taobom.models import Listing, Part, Shop
from taobom.output import render, render_html, render_plan, render_text
from taobom.solver import solve


def build_plan():
    parts = [
        Part("p0", ["R1"], "10k", "0603", 2, lcsc="C1591"),
        Part("p1", ["R2"], "100k", "0603", 1),
    ]
    shops = {"a": Shop("a", "Shop A", "https://shop.example/a", "深圳", 4.9),
             "b": Shop("b", "Shop B"),
             "c": Shop("c", "Shop C")}
    listings = [
        Listing("p0", "a", "10k <b>resistor</b>", price=2, lot_size=10,
                url="https://item.taobao.com/item.htm?id=42", sales=1200,
                confidence=0.6),
        Listing("p1", "b", "100k resistor", price=3,
                url="https://item.taobao.com/item.htm?id=43"),
        Listing("p0", "c", "backup 10k", price=4,
                url="https://item.taobao.com/item.htm?id=44", confidence=0.5),
    ]
    return solve(parts, shops, listings, max_shops=1, shop_penalty=0)


class OutputTests(unittest.TestCase):
    def test_markdown_warns_about_relaxed_cap_and_lists_backups(self):
        text = render_plan(build_plan())
        self.assertIn("至少需要 2 家店铺", text)
        self.assertIn("备选货源", text)
        self.assertIn("C1591", text)
        self.assertIn("已售 1200", text)
        self.assertIn("⚠ 匹配置信度 60%", text)

    def test_html_is_mobile_ready_and_links_into_the_app(self):
        html = render_html(build_plan())
        self.assertIn('name="viewport"', html)
        self.assertIn("taobao://item.taobao.com/item.htm?id=42", html)
        self.assertIn("&lt;b&gt;resistor&lt;/b&gt;", html)
        self.assertNotIn("<b>resistor</b>", html)

    def test_text_output_has_no_markdown(self):
        text = render_text(build_plan())
        self.assertNotIn("**", text)
        self.assertNotIn("##", text)
        self.assertIn("Shop A", text)

    def test_json_output_round_trips(self):
        data = json.loads(render(build_plan(), "json"))
        self.assertEqual(2, data["item_count"])
        self.assertTrue(data["cap_relaxed"])
        self.assertEqual("p0", data["items"][0]["part_id"])
        self.assertEqual("C1591", data["items"][0]["lcsc"])
        self.assertIn("p0", data["alternatives"])

    def test_unknown_format_is_rejected(self):
        with self.assertRaises(ValueError):
            render(build_plan(), "pdf")


if __name__ == "__main__":
    unittest.main()
