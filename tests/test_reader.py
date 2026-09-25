"""The reader against the instrument's own export of the same plate."""

from __future__ import annotations

import unittest

from gemini_res import ResFormatError, calibrator_points, read_run, split_plate, well_name

from .gemini_export import DATA, RUNS, has_export, number, rows


def run_of(name: str):
    return read_run(DATA / (name + ".res"))


class WellNumbering(unittest.TestCase):
    def test_column_major(self):
        # Gemini fills a plate column by column; the export's row order is the
        # proof, and every well index in the file follows it.
        self.assertEqual(well_name(0), "A1")
        self.assertEqual(well_name(1), "B1")
        self.assertEqual(well_name(8), "A2")
        self.assertEqual(well_name(95), "H12")


class EveryRunReads(unittest.TestCase):
    def test_all_files(self):
        for name in RUNS:
            with self.subTest(name):
                run = run_of(name)
                self.assertEqual(len(run.wells), 96)
                self.assertEqual(len(run.od), 8)
                self.assertEqual(len(run.od[0]), 12)
                self.assertTrue(run.assay, "assay name missing")
                self.assertTrue(run.timestamp.year >= 2000)

    def test_truncated_file_is_refused(self):
        # Half a file must not yield half a result. The reader is allowed to
        # fail; it is not allowed to invent.
        raw = (DATA / "320424220465M27LBR.res").read_bytes()
        broken = DATA.parent / "truncated.res"
        broken.write_bytes(raw[: len(raw) // 3])
        try:
            with self.assertRaises(ResFormatError):
                read_run(broken)
        finally:
            broken.unlink()


class HeaderFields(unittest.TestCase):
    def test_known_run(self):
        run = run_of("320424220465M27LBR")
        self.assertEqual(run.plate_id, "320424220465M27LBR")
        # The header names the assay *definition*; the plate ran a copy of it
        # saved under another file name, and that is what the export shows.
        self.assertEqual(run.assay, "3204_Cardiolipin-GM_IgM_V2_QC")
        self.assertEqual(run.assay_name, "3204_Cardiolipin-GM_IgM_27C")
        # The recorded temperature is the instrument's, not the assay's: this
        # plate ran a 27 °C assay in a room at 22.43 °C.
        self.assertEqual(run.temperature, 22.43)
        self.assertEqual(run.unit, "MPL/ml")
        self.assertEqual(
            run.calibrator_concentrations, [0.0, 3.0, 10.0, 30.0, 100.0, 300.0]
        )
        self.assertTrue(run.reagents, "no reagent lots read")
        self.assertTrue(run.events, "no plate events read")
        self.assertLessEqual(run.start, run.end)

    def test_plate_id_matches_the_export(self):
        for name in RUNS:
            if not has_export(name):
                continue
            with self.subTest(name):
                run = run_of(name)
                self.assertEqual(run.plate_id, rows(name)[0]["Plate ID"])
                self.assertEqual(run.assay_name, rows(name)[0]["Assay"])


class OpticalDensities(unittest.TestCase):
    """The core claim: the ODs in the file are the ODs the instrument exported."""

    def test_every_exported_well(self):
        for name in RUNS:
            if not has_export(name):
                continue
            with self.subTest(name):
                run = run_of(name)
                checked = 0
                for row in rows(name):
                    expected = number(row["Reader value"])
                    if expected is None:
                        continue
                    well = run.well(row["Well Location"])
                    self.assertTrue(well.used, "%s not marked as used" % well.name)
                    # The export rounds to three decimals; the file holds the
                    # reader's raw value.
                    self.assertAlmostEqual(well.od, expected, places=3, msg=well.name)
                    checked += 1
                self.assertGreater(checked, 20)

    def test_unused_wells_have_no_value(self):
        run = run_of("340125136490G1RH")  # 32 of 96 wells used
        self.assertEqual(sum(1 for w in run.wells if w.used), 32)
        self.assertTrue(all(w.od is None for w in run.wells if not w.used))


class Layout(unittest.TestCase):
    def test_labels_match_the_export(self):
        # The layout comes out of the CDilutionGroup well maps, not out of an
        # assumption about where calibrators sit.
        for name in ("320424220465M27LBR", "320424220465MRTLBR", "340125136490G1RH"):
            with self.subTest(name):
                run = run_of(name)
                for row in rows(name):
                    if row["Layout Label"].startswith(("Cal", "PC", "NC", "CO")):
                        self.assertEqual(
                            run.well(row["Well Location"]).label,
                            row["Layout Label"],
                            row["Well Location"],
                        )

    def test_sample_ids_match_the_export(self):
        run = run_of("320424220465M27LBR")
        for row in rows(name="320424220465M27LBR"):
            self.assertEqual(
                run.well(row["Well Location"]).sample_id, row["Sample ID"],
                row["Well Location"],
            )

    def test_duplicates_are_bundled(self):
        run = run_of("320424220465M27LBR")
        samples = run.samples()
        self.assertTrue(samples)
        for entry in samples:
            self.assertEqual(len(entry["wells"]), 2, entry["sample_id"])
            self.assertAlmostEqual(
                entry["od_mean"], sum(entry["od"]) / len(entry["od"]), places=9
            )

    def test_means_match_the_export(self):
        run = run_of("320424220465M27LBR")
        mean_of = {}
        for entry in run.samples() + run.controls():
            for well in entry["wells"]:
                mean_of[well] = entry["od_mean"]
        for row in rows("320424220465M27LBR"):
            expected = number(row["Reader mean"])
            if expected is None:
                continue
            # The export rounds half up (0.2755 -> 0.276), Python rounds to
            # even; half of the last exported digit is the honest tolerance.
            self.assertAlmostEqual(
                mean_of[row["Well Location"]], expected, delta=0.0006,
                msg=row["Well Location"],
            )

    def test_calibrator_points(self):
        run = run_of("320424220465M27LBR")
        points = calibrator_points(run)
        # Six calibrators in duplicate, each well on its own.
        self.assertEqual(len(points), 12)
        self.assertEqual(sorted({c for c, _ in points}), run.calibrator_concentrations)


class FlaggedWells(unittest.TestCase):
    """``CResultFlag``: a well the instrument marked is reported, not averaged."""

    def test_the_flag_is_read(self):
        run = run_of("320424220465MRTLBR")
        self.assertEqual([w.name for w in run.wells if w.flagged], ["B4"])

    def test_the_mean_leaves_it_out(self):
        run = run_of("320424220465MRTLBR")
        entry = next(e for e in run.samples() if "B4" in e["wells"])
        self.assertEqual(entry["wells"], ["A4", "B4"])
        self.assertEqual([round(v, 3) for v in entry["od_excluded"]], [0.027])
        self.assertAlmostEqual(entry["od_mean"], run.well("A4").od, places=9)
        # And that is what the instrument exported for both wells of the pair.
        for row in rows("320424220465MRTLBR"):
            if row["Well Location"] in ("A4", "B4"):
                self.assertAlmostEqual(number(row["Reader mean"]), 0.079, places=3)

    def test_no_other_run_has_one(self):
        for name in RUNS:
            if name == "320424220465MRTLBR":
                continue
            with self.subTest(name):
                self.assertFalse([w.name for w in run_of(name).wells if w.flagged])


class HandPipetted(unittest.TestCase):
    """A run without a pipetting protocol: the file does not name the controls."""

    def test_says_so_instead_of_guessing(self):
        run = run_of("320625480manGM5aLKE")
        self.assertFalse(run.groups)
        self.assertTrue(any("no pipetting protocol" in gap for gap in run.gaps))
        unnamed = [w for w in run.wells if w.label == "C?"]
        self.assertTrue(unnamed)
        self.assertTrue(all(w.used and not w.is_sample for w in unnamed))

    def test_samples_are_still_read(self):
        run = run_of("320625480manGM5aLKE")
        self.assertTrue(run.samples())
        self.assertTrue(all(entry["sample_id"] for entry in run.samples()))


class SplitPlate(unittest.TestCase):
    def test_two_assays_are_recognised(self):
        run = run_of("320426350494GM03LBR3")
        self.assertTrue(split_plate(run))
        self.assertGreater(len(run.assays), 1)

    def test_a_single_assay_plate_is_not(self):
        self.assertFalse(split_plate(run_of("320424220465M27LBR")))


if __name__ == "__main__":
    unittest.main()
