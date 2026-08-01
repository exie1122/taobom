import unittest

from taobom.models import Part
from taobom.scrape import _infer_lot_size, _match, _shop_slug


class ScrapeMatchingTests(unittest.TestCase):
    def part(self):
        return Part(
            id="ldo", refs=["U1"], value="XC6206P182 1.8V",
            footprint="Package_TO_SOT_SMD:SOT-23", qty=1,
            mpn="XC6206P182", package="SOT-23",
            search_terms=["XC6206P182 1.8V SOT-23"],
        )

    def test_exact_part_with_matching_attributes(self):
        confidence, reasons = _match(
            self.part(), "XC6206P182 1.8V 250mA SOT-23 稳压器"
        )
        self.assertEqual(1.0, confidence)
        self.assertIn("exact MPN", reasons)

    def test_conflicting_voltage_is_rejected_even_with_mpn(self):
        confidence, reasons = _match(
            self.part(), "XC6206P182 2.8V 250mA SOT-23 稳压器"
        )
        self.assertEqual(0.0, confidence)
        self.assertIn("conflicting voltage", reasons[0])

    def test_shop_ids_cannot_collide_after_normalization(self):
        self.assertNotEqual(_shop_slug("foo bar"), _shop_slug("foo-bar"))

    def test_pin_and_pin_abbreviations_are_equivalent(self):
        part = Part(
            "fpc", [], "connector", "", 1, package="FPC 24P 0.5mm",
            search_terms=["24P 0.5mm FPC connector"],
        )
        confidence, reasons = _match(
            part, "Hirose 24Pin 0.5mm FPC connector"
        )
        self.assertGreater(confidence, 0)
        self.assertNotIn("conflicting pin count", " ".join(reasons))

    def test_resistor_array_is_not_a_discrete_resistor(self):
        part = Part(
            "10k", ["R1"], "10k 1%", "0603", 1, package="0603",
            search_terms=["贴片电阻 0603 10k"],
        )
        confidence, reasons = _match(
            part, "1206(0603×4)贴片排阻1% 1K 10K 100K"
        )
        self.assertEqual(0, confidence)
        self.assertIn("array", reasons[0])

    def test_conflicting_output_current_is_rejected(self):
        part = Part(
            "ldo", ["U1"], "XC6206P152MR 1.5V LDO 200mA", "SOT-23", 1,
            package="SOT-23", search_terms=["XC6206P152MR 1.5V SOT-23"],
        )
        confidence, reasons = _match(
            part, "XC6206P152MR 1.5V 300mA SOT-23"
        )
        self.assertEqual(0, confidence)
        self.assertIn("conflicting current", reasons[0])

    def test_parenthesized_pack_size_is_extracted(self):
        self.assertEqual(10, _infer_lot_size("B5819WS SOD-323（10只）"))
        self.assertEqual(100, _infer_lot_size("0603 4.7kΩ ±1%(发100只)"))
        self.assertEqual(1, _infer_lot_size("24P FPC connector"))


if __name__ == "__main__":
    unittest.main()
