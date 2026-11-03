import contextlib
import csv
import io
import tempfile
import unittest
from pathlib import Path

from taobom.bom import BomError, _expand_refs, apply_overrides, load_bom


def write_bom(directory: Path, header, rows, name="bom.csv") -> Path:
    path = directory / name
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(header)
        writer.writerows(rows)
    return path


KICAD = ["Reference", "Value", "Footprint", "MPN"]


class BomParsingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_grouped_kicad_rows_and_boards_multiplier(self):
        path = write_bom(self.dir, KICAD, [
            ["C2-C7", "0.1uF", "Capacitor_SMD:C_0603_1608Metric", ""],
            ["U1", "CORE1106G3", "Core1106-PCB:Core1106-SMT", ""],
            ["TP1", "GND", "TestPoint:TestPoint_Pad_D1.0mm", ""],
        ])
        parts = load_bom(path)
        self.assertEqual(["0.1uF_0603", "CORE1106G3"], [p.id for p in parts])
        self.assertEqual(6, parts[0].qty)
        self.assertEqual("0603", parts[0].package)

        doubled = load_bom(path, boards=2)
        self.assertEqual(12, doubled[0].qty)
        self.assertEqual(2, doubled[1].qty)

    def test_ungrouped_rows_are_merged_instead_of_duplicating_ids(self):
        path = write_bom(self.dir, KICAD, [
            ["C1", "0.1uF", "Capacitor_SMD:C_0603_1608Metric", ""],
            ["C2", "0.1uF", "Capacitor_SMD:C_0603_1608Metric", ""],
            ["C3", "0.1uF", "Capacitor_SMD:C_0603_1608Metric", ""],
        ])
        parts = load_bom(path)
        self.assertEqual(1, len(parts))
        self.assertEqual(3, parts[0].qty)
        self.assertEqual(["C1", "C2", "C3"], parts[0].refs)

    def test_jlcpcb_export_is_accepted_and_lcsc_is_kept(self):
        path = write_bom(self.dir, [
            "Comment", "Designator", "Footprint", "LCSC Part #",
        ], [
            ["100nF 50V X7R", "C1,C2", "C_0603_1608Metric", "C1591"],
        ])
        parts = load_bom(path)
        self.assertEqual(1, len(parts))
        part = parts[0]
        self.assertEqual("100nF 50V X7R", part.value)
        self.assertEqual("C1591", part.lcsc)
        self.assertIn("贴片电容 0603 100nF", part.search_terms)
        self.assertIn("C1591", part.search_terms)

    def test_dnp_rows_are_skipped(self):
        path = write_bom(self.dir, KICAD + ["DNP"], [
            ["R1", "10k", "Resistor_SMD:R_0603_1608Metric", "", "DNP"],
            ["R2", "10k", "Resistor_SMD:R_0603_1608Metric", "", ""],
        ])
        parts = load_bom(path)
        self.assertEqual(1, len(parts))
        self.assertEqual(["R2"], parts[0].refs)
        self.assertEqual(1, parts[0].qty)

    def test_slug_collisions_stay_unique(self):
        path = write_bom(self.dir, KICAD, [
            ["C1", "1.0 uF", "C_0603_1608Metric", ""],
            ["C2", "1.0_uF", "C_0603_1608Metric", ""],
        ])
        ids = [p.id for p in load_bom(path)]
        self.assertEqual(len(ids), len(set(ids)))

    def test_missing_reference_column_explains_the_export_command(self):
        path = write_bom(self.dir, ["Comment", "Footprint"], [["0.1uF", "C_0603"]])
        with self.assertRaises(BomError) as ctx:
            load_bom(path)
        self.assertIn("kicad-cli sch export bom", str(ctx.exception))

    def test_only_non_components_is_an_error_not_empty_success(self):
        path = write_bom(self.dir, KICAD, [
            ["TP1", "GND", "TestPoint:TestPoint_Pad_D1.0mm", ""],
        ])
        with self.assertRaises(BomError) as ctx:
            load_bom(path)
        self.assertIn("No purchasable parts", str(ctx.exception))

    def test_missing_file_is_a_clear_error(self):
        with self.assertRaises(FileNotFoundError):
            load_bom(self.dir / "nope.csv")

    def test_reference_range_typos_are_not_expanded(self):
        self.assertEqual(["R1-R20000"], _expand_refs("R1-R20000"))
        self.assertEqual(["C2", "C3", "C4"], _expand_refs("C2-C4"))


class OverridesTests(unittest.TestCase):
    def setUp(self):
        self.parts = load_bom(Path(__file__).resolve().parents[1] / "core1106_stereo_bom.csv")

    def test_unknown_kit_coverage_warns_but_applies(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            parts = apply_overrides(self.parts, {
                "kits": [{"id": "kit", "value": "样品本", "covers": ["0.1uF_0603", "nope"]}],
            })
        self.assertIn("nope", err.getvalue())
        self.assertIn("kit", [p.id for p in parts])
        self.assertNotIn("0.1uF_0603", [p.id for p in parts])

    def test_bad_quantities_are_rejected(self):
        with self.assertRaises(ValueError):
            apply_overrides(self.parts, {"qty": {"CORE1106G3": 0}})
        with self.assertRaises(ValueError):
            apply_overrides(self.parts, {"qty": {"CORE1106G3": "3"}})

    def test_added_part_cannot_reuse_an_existing_id(self):
        with self.assertRaises(ValueError):
            apply_overrides(self.parts, {
                "add": [{"id": "CORE1106G3", "value": "x", "qty": 1}],
            })

    def test_wrong_section_types_are_rejected(self):
        with self.assertRaises(ValueError):
            apply_overrides(self.parts, {"exclude": "CORE1106G3"})
        with self.assertRaises(ValueError):
            apply_overrides(self.parts, {"qty": ["CORE1106G3"]})
        with self.assertRaises(ValueError):
            apply_overrides(self.parts, {"kits": {"id": "x"}})

    def test_mpn_only_rows_still_get_a_usable_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_bom(Path(tmp), ["Reference", "MPN", "Footprint"], [
                ["U1", "XC6206P182", "Package_TO_SOT_SMD:SOT-23"],
            ])
            parts = load_bom(path)
        self.assertEqual("XC6206P182_SOT_23", parts[0].id)
        self.assertEqual(["XC6206P182"], parts[0].search_terms[:1])


if __name__ == "__main__":
    unittest.main()
