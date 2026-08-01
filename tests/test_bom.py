import csv
import json
import tempfile
import unittest
from pathlib import Path

from taobom.bom import apply_overrides, load_bom


class BomTests(unittest.TestCase):
    def write_bom(self, rows, fieldnames=("Reference", "Value", "Footprint", "MPN")):
        tmp = tempfile.TemporaryDirectory()
        path = Path(tmp.name) / "bom.csv"
        with path.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
        self.addCleanup(tmp.cleanup)
        return path

    def test_duplicate_rows_are_consolidated(self):
        path = self.write_bom([
            {"Reference": "C1,C2", "Value": "1uF",
             "Footprint": "Capacitor_SMD:C_0603", "MPN": ""},
            {"Reference": "C3", "Value": "1uF",
             "Footprint": "Capacitor_SMD:C_0603", "MPN": ""},
        ])
        parts = load_bom(path)
        self.assertEqual(1, len(parts))
        self.assertEqual(["C1", "C2", "C3"], parts[0].refs)
        self.assertEqual(3, parts[0].qty)
        self.assertEqual("1uF_0603", parts[0].id)

    def test_real_identity_collisions_get_stable_unique_ids(self):
        path = self.write_bom([
            {"Reference": "U1", "Value": "REG", "Footprint": "A:SOT-23", "MPN": "A"},
            {"Reference": "U2", "Value": "REG", "Footprint": "B:SOT-23", "MPN": "B"},
        ])
        ids = [part.id for part in load_bom(path)]
        self.assertEqual(2, len(set(ids)))
        self.assertTrue(all(pid.startswith("REG_SOT_23__") for pid in ids))

    def test_missing_required_column_is_actionable(self):
        path = self.write_bom(
            [{"Reference": "R1", "Value": "1k"}],
            fieldnames=("Reference", "Value"),
        )
        with self.assertRaisesRegex(ValueError, "Footprint"):
            load_bom(path)

    def test_jlcpcb_column_names_are_supported(self):
        path = self.write_bom(
            [{
                "Designator": "C1,C2",
                "Comment": "100nF 50V X7R",
                "Footprint": "0603",
                "LCSC Part #": "C1591",
            }],
            fieldnames=("Comment", "Designator", "Footprint", "LCSC Part #"),
        )
        parts = load_bom(path)
        self.assertEqual(["C1", "C2"], parts[0].refs)
        self.assertEqual("100nF 50V X7R", parts[0].value)
        self.assertEqual("", parts[0].mpn)
        self.assertEqual("贴片电容 0603 100nF", parts[0].search_terms[0])

    def test_override_rejects_duplicate_ids(self):
        path = self.write_bom([
            {"Reference": "R1", "Value": "1k",
             "Footprint": "Resistor_SMD:R_0603", "MPN": ""},
        ])
        with self.assertRaisesRegex(ValueError, "duplicate part ids"):
            apply_overrides(load_bom(path), {
                "add": [{"id": "1k_0603", "value": "other", "qty": 1}]
            })


if __name__ == "__main__":
    unittest.main()
