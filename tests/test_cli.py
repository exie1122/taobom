import contextlib
import csv
import io
import json
import tempfile
import unittest
from pathlib import Path

from taobom.cli import load_listings, main, merge_listings, save_listings
from taobom.models import Listing, Shop

REPO = Path(__file__).resolve().parents[1]
BOM = REPO / "core1106_stereo_bom.csv"


def run(*argv: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(list(argv))
    return code, out.getvalue(), err.getvalue()


class CliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_parts_json_is_machine_readable_and_honours_boards(self):
        code, out, _ = run("parts", str(BOM), "--json", "--boards", "2")
        self.assertEqual(0, code)
        parts = {p["id"]: p for p in json.loads(out)}
        self.assertEqual(12, parts["0.1uF_0603"]["qty"])
        self.assertEqual(2, parts["CORE1106G3"]["qty"])

    def test_parts_text_finishes_with_a_summary(self):
        code, out, _ = run("parts", str(BOM), "--boards", "2")
        self.assertEqual(0, code)
        self.assertIn("10 parts · 42 units · 2 boards", out)

    def test_bad_bom_exits_2_with_an_actionable_message(self):
        path = self.dir / "bad.csv"
        with path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["Comment", "Footprint"])
            writer.writerow(["0.1uF", "C_0603"])
        code, _, err = run("parts", str(path))
        self.assertEqual(2, code)
        self.assertIn("error:", err)
        self.assertIn("kicad-cli", err)

    def test_missing_bom_exits_2(self):
        code, _, err = run("parts", str(self.dir / "nope.csv"))
        self.assertEqual(2, code)
        self.assertIn("not found", err)

    def test_mock_then_plan_writes_html(self):
        listings = self.dir / "listings.json"
        code, _, _ = run("mock", str(BOM), "-o", str(listings))
        self.assertEqual(0, code)
        output = self.dir / "plan.html"
        code, out, _ = run("plan", str(BOM), str(listings), "-o", str(output))
        self.assertEqual(0, code)
        self.assertTrue(output.is_file())
        self.assertIn("<!doctype html>", output.read_text(encoding="utf-8"))
        self.assertIn("total ~ CNY", out)

    def test_plan_json_output(self):
        listings = self.dir / "listings.json"
        run("mock", str(BOM), "-o", str(listings))
        output = self.dir / "plan.json"
        code, _, _ = run("plan", str(BOM), str(listings), "-o", str(output))
        self.assertEqual(0, code)
        data = json.loads(output.read_text(encoding="utf-8"))
        self.assertIn("total", data)
        self.assertTrue(data["items"])

    def test_strict_shop_cap_exits_1(self):
        listings = self.dir / "listings.json"
        run("mock", str(BOM), "-o", str(listings))
        code, _, err = run("plan", str(BOM), str(listings),
                           "--max-shops", "1", "--strict-shops",
                           "-o", str(self.dir / "p.md"))
        self.assertEqual(1, code)
        self.assertIn("at least 2 shops", err)

    def test_listings_validation_reports_unknown_fields(self):
        path = self.dir / "listings.json"
        path.write_text(json.dumps({
            "shops": [{"id": "s", "name": "S"}],
            "listings": [{"part_id": "p", "shop_id": "s", "title": "t",
                          "bogus": 1}],
        }), encoding="utf-8")
        with self.assertRaises(ValueError) as ctx:
            load_listings(path)
        self.assertIn("bogus", str(ctx.exception))

    def test_listings_validation_rejects_unknown_shop(self):
        path = self.dir / "listings.json"
        path.write_text(json.dumps({
            "shops": [{"id": "s", "name": "S"}],
            "listings": [{"part_id": "p", "shop_id": "ghost", "title": "t"}],
        }), encoding="utf-8")
        with self.assertRaises(ValueError):
            load_listings(path)

    def test_merge_matches_shops_by_name(self):
        base = {"youxin": Shop("youxin", "优信电子元器件")}
        shops, listings = merge_listings(
            base, [],
            [Shop("优信电子元器件", "优信电子元器件")],
            [Listing("p", "优信电子元器件", "t")],
        )
        self.assertEqual(["youxin"], [s.id for s in shops])
        self.assertEqual("youxin", listings[0].shop_id)

    def test_save_listings_is_atomic(self):
        path = self.dir / "out" / "listings.json"
        save_listings(path, [Shop("s", "S")], [Listing("p", "s", "t")])
        self.assertTrue(path.is_file())
        self.assertEqual([], list(path.parent.glob("*.partial")))
        shops, listings = load_listings(path)
        self.assertEqual(["s"], list(shops))
        self.assertEqual(1, len(listings))


if __name__ == "__main__":
    unittest.main()
