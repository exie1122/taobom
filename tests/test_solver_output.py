import unittest

from taobom.models import Listing, Part, Shop
from taobom.output import render_plan
from taobom.solver import solve


class SolverOutputTests(unittest.TestCase):
    def test_free_shipping_threshold_affects_selection(self):
        parts = [
            Part("p1", ["R1"], "P1", "", 1),
            Part("p2", ["R2"], "P2", "", 1),
        ]
        shops = {
            "threshold": Shop("threshold", "Threshold", ship_fee=10,
                              free_ship_over=10),
            "ordinary": Shop("ordinary", "Ordinary"),
        }
        listings = [
            Listing("p1", "threshold", "P1", price=6),
            Listing("p2", "threshold", "P2", price=6),
            Listing("p1", "ordinary", "P1", price=7),
            Listing("p2", "ordinary", "P2", price=7),
        ]
        plan = solve(parts, shops, listings, max_shops=1, shop_penalty=0)
        self.assertEqual({"threshold"}, set(plan.by_shop()))
        self.assertEqual(12, plan.total)

    def test_output_explains_excess_and_unverified_variant(self):
        part = Part("r", ["R1", "R2", "R3"], "1k", "R_0603", 3)
        shop = Shop("s", "Shop")
        listing = Listing("r", "s", "1k resistor", price=2, lot_size=10,
                          source_query="0603 1k", match_reasons=["package matches"])
        plan = solve([part], {"s": shop}, [listing])
        text = render_plan(plan)
        self.assertIn("余量 7 个", text)
        self.assertIn("规格未核验", text)
        self.assertIn("匹配依据: package matches", text)


if __name__ == "__main__":
    unittest.main()
