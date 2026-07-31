import json
import tempfile
import unittest
from pathlib import Path

from taobom.gui import (
    discover_kicad_schematics,
    _estimated_parts_total,
    _item_id,
    _links_text,
    _state_key,
    load_session,
    load_recent_sessions,
    make_qr_image,
    remember_session,
    shop_color,
)
from taobom.models import Listing


class GuiDataTests(unittest.TestCase):
    def test_session_resolves_relative_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "session.json"
            path.write_text(json.dumps({
                "bom": "board.csv",
                "listings": "selected.json",
                "plan": "plan.txt",
                "max_shops": 7,
            }), encoding="utf-8")
            session = load_session(path)
            self.assertEqual(root / "board.csv", session.bom)
            self.assertEqual(root / "selected.json", session.listings)
            self.assertEqual(7, session.max_shops)

    def test_cart_state_is_part_specific(self):
        first = Listing("r1", "shop", "same", url="https://item/1")
        second = Listing("r2", "shop", "same", url="https://item/1")
        self.assertNotEqual(_state_key(first), _state_key(second))

    def test_copy_all_links_contains_only_urls(self):
        listings = [
            Listing("r1", "shop", "first title", url="https://item/1"),
            Listing("r2", "shop", "second title", url="https://item/2"),
        ]
        self.assertEqual(
            "https://item/1\nhttps://item/2",
            _links_text(listings),
        )

    def test_qr_generation_and_item_id(self):
        url = "https://item.taobao.com/item.htm?id=123456"
        image = make_qr_image(url)
        self.assertGreater(image.width, 100)
        self.assertEqual("123456", _item_id(url))

    def test_estimated_total_uses_lots_not_board_quantity(self):
        listings = [
            Listing("cap", "shop", "50 pack", price=5, lot_size=50),
            Listing("fpc", "shop", "single", price=1.2, lot_size=1),
        ]
        parts = {
            "cap": type("PartLike", (), {"qty": 6})(),
            "fpc": type("PartLike", (), {"qty": 2})(),
        }
        self.assertEqual(7.4, _estimated_parts_total(listings, parts))

    def test_kicad_discovery_prefers_main_project_and_ignores_backups(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            main = root / "board.kicad_sch"
            main.write_text("", encoding="utf-8")
            (root / "board.kicad_pro").write_text("", encoding="utf-8")
            nested = root / "sub" / "sheet.kicad_sch"
            nested.parent.mkdir()
            nested.write_text("", encoding="utf-8")
            backup = root / "backups" / "old.kicad_sch"
            backup.parent.mkdir()
            backup.write_text("", encoding="utf-8")
            found = discover_kicad_schematics(root)
            self.assertEqual(main, found[0])
            self.assertNotIn(backup, found)

    def test_recent_sessions_are_deduplicated(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            registry = root / "recent.json"
            first = root / "one" / "session.json"
            second = root / "two" / "session.json"
            first.parent.mkdir()
            second.parent.mkdir()
            first.write_text("{}", encoding="utf-8")
            second.write_text("{}", encoding="utf-8")
            remember_session(first, registry)
            remember_session(second, registry)
            remember_session(first, registry)
            self.assertEqual([first, second], load_recent_sessions(registry))

    def test_shop_colors_do_not_repeat(self):
        colors = [shop_color(index) for index in range(24)]
        self.assertEqual(len(colors), len(set(colors)))


if __name__ == "__main__":
    unittest.main()
