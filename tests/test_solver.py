import unittest
from unittest import mock

from taobom.models import Listing, Part, Shop
from taobom.solver import _solve_greedy, solve


def make_parts(n: int) -> list[Part]:
    return [Part(f"p{i}", [f"R{i}"], f"v{i}", "", 1) for i in range(n)]


def split_shops(n: int) -> tuple[dict[str, Shop], list[Listing]]:
    shops = {"a": Shop("a", "A"), "b": Shop("b", "B")}
    listings = [
        Listing(f"p{i}", "a" if i % 2 == 0 else "b", f"item {i}", price=1.0)
        for i in range(n)
    ]
    return shops, listings


class SolverTests(unittest.TestCase):
    def test_impossible_shop_cap_is_relaxed_and_reported(self):
        parts = make_parts(4)
        shops, listings = split_shops(4)
        plan = solve(parts, shops, listings, max_shops=1, shop_penalty=0)
        self.assertEqual(4, len(plan.assignments))
        self.assertEqual(2, len(plan.by_shop()))
        self.assertTrue(plan.cap_relaxed)
        self.assertEqual(2, plan.min_shops_required)

    def test_strict_shop_cap_fails_with_the_number_needed(self):
        parts = make_parts(4)
        shops, listings = split_shops(4)
        with self.assertRaises(RuntimeError) as ctx:
            solve(parts, shops, listings, max_shops=1, strict_shops=True)
        self.assertIn("at least 2 shops", str(ctx.exception))
        self.assertIn("--max-shops", str(ctx.exception))

    def test_zero_shop_cap_means_unlimited(self):
        parts = make_parts(4)
        shops, listings = split_shops(4)
        plan = solve(parts, shops, listings, max_shops=0, shop_penalty=0)
        self.assertEqual(4, len(plan.assignments))
        self.assertFalse(plan.cap_relaxed)

    def test_greedy_fallback_keeps_every_part_when_relaxed(self):
        parts = make_parts(4)
        shops, listings = split_shops(4)
        with mock.patch("taobom.solver._solve_ilp", side_effect=ImportError):
            plan = solve(parts, shops, listings, max_shops=1, shop_penalty=0)
        self.assertEqual(4, len(plan.assignments))
        self.assertEqual([], plan.unmatched)
        self.assertEqual(2, len(plan.by_shop()))
        self.assertEqual("greedy", plan.engine)

    def test_greedy_reports_parts_it_cannot_cover_within_the_cap(self):
        parts = make_parts(4)
        shops, listings = split_shops(4)
        by_part = {p.id: [l for l in listings if l.part_id == p.id] for p in parts}
        assignments, leftover = _solve_greedy(parts, shops, by_part, 1, 0, 15)
        self.assertEqual(2, len(assignments))
        self.assertEqual(2, len(leftover))

    def test_missing_shop_is_a_clear_error(self):
        parts = make_parts(1)
        with self.assertRaises(ValueError):
            solve(parts, {}, [Listing("p0", "ghost", "x")])

    def test_alternatives_exclude_the_chosen_listing(self):
        part = Part("p0", ["R1"], "10k", "0603", 1)
        shops = {"a": Shop("a", "A"), "b": Shop("b", "B")}
        listings = [
            Listing("p0", "a", "cheap", price=1),
            Listing("p0", "b", "backup", price=2),
            Listing("p0", "b", "backup 2", price=3),
        ]
        plan = solve([part], shops, listings, shop_penalty=0)
        chosen = plan.assignments[0].listing
        self.assertEqual("cheap", chosen.title)
        self.assertEqual(2, len(plan.alternatives["p0"]))
        self.assertNotIn(chosen, plan.alternatives["p0"])

    def test_single_shop_comparison_reports_real_savings(self):
        parts = [Part("p0", ["R1"], "10k", "0603", 1),
                 Part("p1", ["R2"], "100k", "0603", 1)]
        shops = {"a": Shop("a", "A"), "b": Shop("b", "B")}
        listings = [
            Listing("p0", "a", "p0 at a", price=1),
            Listing("p0", "b", "p0 at b", price=100),
            Listing("p1", "a", "p1 at a", price=100),
            Listing("p1", "b", "p1 at b", price=1),
        ]
        plan = solve(parts, shops, listings, max_shops=2, shop_penalty=0)
        self.assertEqual(2, len(plan.by_shop()))
        self.assertIsNotNone(plan.single_shop)
        self.assertAlmostEqual(101.0, plan.single_shop[1])
        saving = plan.savings_vs_single_shop()
        self.assertIsNotNone(saving)
        self.assertAlmostEqual(99.0, saving[1])

    def test_free_shipping_threshold_is_applied_to_the_baseline(self):
        parts = [Part("p0", ["R1"], "10k", "0603", 1)]
        shops = {"a": Shop("a", "A", free_ship_over=10, ship_fee=5)}
        listings = [Listing("p0", "a", "x", price=12)]
        plan = solve(parts, shops, listings)
        self.assertEqual(12, plan.single_shop[1])


if __name__ == "__main__":
    unittest.main()
