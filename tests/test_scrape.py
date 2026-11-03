import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from taobom.models import Part
from taobom.scrape import (
    ShopRegistry,
    _score,
    infer_lot_size,
    listings_for_part,
    scrape,
)


def payload_for(title: str, price: str = "3.5") -> dict:
    return {
        "data": {
            "itemList": [{
                "title": title,
                "price": price,
                "itemId": "777",
                "nick": "深圳集芯电子",
                "procity": "深圳",
                "realSales": "1.2万",
                "stock": "500",
                "shopLink": "https://shop123.taobao.com",
            }]
        }
    }


class ScrapeTests(unittest.TestCase):
    def test_shop_ids_do_not_collide_after_normalisation(self):
        registry = ShopRegistry()
        first = registry.add("foo bar")
        second = registry.add("foo-bar")
        self.assertNotEqual(first.id, second.id)
        self.assertEqual(first.id, registry.add("foo bar").id)
        self.assertEqual(2, len(registry.shops))

    def test_lot_size_only_trusts_explicit_pack_wording(self):
        self.assertEqual(10, infer_lot_size("B5819WS SOD-323（10只）"))
        self.assertEqual(100, infer_lot_size("0603 4.7k 100个/包"))
        self.assertEqual(50, infer_lot_size("每包 50 个"))
        self.assertEqual(1, infer_lot_size("24P FPC connector"))
        self.assertEqual(1, infer_lot_size("1个装"))

    def test_mpn_hit_scores_full_confidence(self):
        part = Part("ldo", ["U1"], "XC6206P182 1.8V", "SOT-23", 1,
                    mpn="XC6206P182", package="SOT-23",
                    search_terms=["XC6206P182 1.8V SOT-23"])
        self.assertEqual(1.0, _score(part, "XC6206P182 1.8V LDO"))
        self.assertLess(_score(part, "some other regulator"), 1.0)

    def test_payload_extraction_fills_optional_signals(self):
        part = Part("ldo", ["U1"], "XC6206P182 1.8V", "SOT-23", 1,
                    mpn="XC6206P182", package="SOT-23",
                    search_terms=["XC6206P182 1.8V SOT-23"])
        registry = ShopRegistry()
        listings = listings_for_part(part, [payload_for(
            "XC6206P182 1.8V SOT-23 10只装")], registry, query="XC6206P182")
        self.assertEqual(1, len(listings))
        listing = listings[0]
        self.assertEqual("https://item.taobao.com/item.htm?id=777", listing.url)
        self.assertEqual(10, listing.lot_size)
        self.assertEqual(12000, listing.sales)
        self.assertEqual(500, listing.stock)
        self.assertEqual(1.0, listing.confidence)
        self.assertEqual("XC6206P182", listing.source_query)
        self.assertEqual("深圳集芯电子", registry.shops[0].name)

    def test_min_confidence_filters_weak_matches(self):
        part = Part("ldo", ["U1"], "XC6206P182 1.8V", "SOT-23", 1,
                    mpn="XC6206P182", package="SOT-23",
                    search_terms=["XC6206P182 1.8V SOT-23"])
        registry = ShopRegistry()
        self.assertEqual([], listings_for_part(
            part, [payload_for("unrelated gadget")], registry, min_confidence=0.9))

    def test_reuse_raw_reads_cache_without_launching_a_browser(self):
        part = Part("ldo", ["U1"], "XC6206P182 1.8V", "SOT-23", 1,
                    mpn="XC6206P182", package="SOT-23",
                    search_terms=["XC6206P182 1.8V SOT-23"])
        with tempfile.TemporaryDirectory() as tmp:
            raw = Path(tmp) / "raw"
            raw.mkdir()
            (raw / f"{part.id}.json").write_text(
                json.dumps([payload_for("XC6206P182 1.8V SOT-23")]),
                encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()):
                shops, listings = scrape([part], raw, reuse_raw=True)
        self.assertEqual(1, len(shops))
        self.assertEqual(1, len(listings))
        self.assertEqual("XC6206P182 1.8V SOT-23", listings[0].title)

    def test_partial_cache_scrapes_only_the_missing_part(self):
        # No browser must be launched when the only part is cached, even with
        # reuse_raw and several parts requested.
        cached = Part("cached", ["U1"], "XC6206P182 1.8V", "SOT-23", 1,
                      mpn="XC6206P182", package="SOT-23",
                      search_terms=["XC6206P182 1.8V SOT-23"])
        with tempfile.TemporaryDirectory() as tmp:
            raw = Path(tmp) / "raw"
            raw.mkdir()
            (raw / f"{cached.id}.json").write_text(
                json.dumps([payload_for("XC6206P182 1.8V SOT-23")]),
                encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()):
                shops, listings = scrape([cached], raw, reuse_raw=True, only=["cached"])
        self.assertEqual(1, len(listings))

    def test_unknown_only_part_is_rejected(self):
        part = Part("p", ["R1"], "10k", "0603", 1)
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                scrape([part], Path(tmp) / "raw", reuse_raw=True, only=["nope"])


if __name__ == "__main__":
    unittest.main()
