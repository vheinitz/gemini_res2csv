"""The CSV this package writes, against the CSV the instrument wrote."""

from __future__ import annotations

import csv
import io
import unittest

from gemini_res import export_csv, read_run
from gemini_res.csv_export import COLUMNS

from .gemini_export import DATA, RUNS, has_export, number, rows


def run_of(name: str):
    return read_run(DATA / (name + ".res"))


def parse(text: str) -> list[dict]:
    lines = text.splitlines()
    head = next(i for i, line in enumerate(lines) if line.startswith("Assay,Plate ID,"))
    return list(csv.DictReader(lines[head:]))


class Shape(unittest.TestCase):
    def test_header_and_columns(self):
        run = run_of("900124220465M27LBR")
        text = export_csv(run)
        self.assertTrue(text.startswith("Plate ID,900124220465M27LBR\r\n"))
        self.assertIn("Assay,9001_Cardiolipin-GM_IgM_27C\r\n", text)
        self.assertEqual(list(parse(text)[0].keys()), COLUMNS)

    def test_crlf_and_quoting(self):
        # Gemini writes CRLF, leaves the two header lines and the column names
        # unquoted and quotes every data field; a tool that already reads its
        # files should not need a special case for ours.
        text = export_csv(run_of("900325136490G1RH"))
        self.assertIn('"9003_a-TPO_IgG_V3_QC","900325136490G1RH","A1","CalA"', text)
        self.assertIn("\r\nAssay,Plate ID,Well Location,", text)
        self.assertNotIn("\n", text.replace("\r\n", ""))

    def test_the_head_is_the_instrument_s_head(self):
        for name in ("900124220465M27LBR", "900325136490G1RH"):
            with self.subTest(name):
                theirs = (DATA / (name + ".csv")).read_bytes()
                ours = export_csv(run_of(name)).encode("cp1252")
                cut = theirs.index(b"\r\n", theirs.index(b"Quant. 1 mean"))
                self.assertEqual(ours[:cut], theirs[:cut])

    def test_one_row_per_used_well(self):
        for name in RUNS:
            with self.subTest(name):
                run = run_of(name)
                out = parse(export_csv(run))
                self.assertEqual(len(out), sum(1 for w in run.wells if w.used))
                if has_export(name):
                    self.assertEqual(
                        [row["Well Location"] for row in out],
                        [row["Well Location"] for row in rows(name)],
                    )

    def test_every_run_exports(self):
        # Including the split plate and the hand-pipetted run: there the
        # quantitative columns read ***** , but the file is still written.
        for name in RUNS:
            with self.subTest(name):
                text = export_csv(run_of(name))
                self.assertTrue(parse(text))


class AgainstTheInstrument(unittest.TestCase):
    def test_values_and_labels(self):
        for name in ("900124220465M27LBR", "900124220465MRTLBR", "900325136490G1RH"):
            with self.subTest(name):
                ours = {row["Well Location"]: row for row in parse(export_csv(run_of(name)))}
                for theirs in rows(name):
                    row = ours[theirs["Well Location"]]
                    self.assertEqual(row["Layout Label"], theirs["Layout Label"])
                    self.assertEqual(row["Sample ID"], theirs["Sample ID"])
                    for column in ("Reader value", "Reader mean"):
                        self.assertAlmostEqual(
                            number(row[column]), number(theirs[column]), delta=0.0011,
                            msg="%s %s %s" % (name, theirs["Well Location"], column),
                        )

    def test_stars_where_the_instrument_writes_stars(self):
        # ***** means "no value" — outside the curve. Where the instrument wrote
        # it, we must not produce a number out of thin air.
        run = run_of("900124220465M27LBR")
        ours = {row["Well Location"]: row for row in parse(export_csv(run))}
        for theirs in rows("900124220465M27LBR"):
            if number(theirs["Quant. 1 value"]) is None:
                self.assertIsNone(
                    number(ours[theirs["Well Location"]]["Quant. 1 value"]),
                    theirs["Well Location"],
                )

    def test_a_split_plate_writes_no_quantitative_values(self):
        run = run_of("900126350494GM03LBR3")
        for row in parse(export_csv(run)):
            self.assertEqual(row["Quant. 1 value"], "*****")
            self.assertEqual(row["Quant. 1 mean"], "*****")

    def test_without_a_layout_there_is_no_mean(self):
        run = run_of("900225480manGM5aLKE")
        controls = [row for row in parse(export_csv(run)) if row["Layout Label"] == "C?"]
        self.assertTrue(controls)
        for row in controls:
            self.assertEqual(row["Reader mean"], "*****")
            self.assertNotEqual(row["Reader value"], "*****")


class CommandLine(unittest.TestCase):
    def test_csv_to_stdout(self):
        from gemini_res.cli import main

        out = io.StringIO()
        import contextlib

        with contextlib.redirect_stdout(out):
            code = main([str(DATA / "900325136490G1RH.res")])
        self.assertEqual(code, 0)
        self.assertEqual(out.getvalue(), export_csv(run_of("900325136490G1RH")))

    def test_summary_of_a_directory(self):
        from gemini_res.cli import main

        import contextlib

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = main(["--format", "summary", str(DATA)])
        self.assertEqual(code, 0)
        text = out.getvalue()
        for name in RUNS:
            self.assertIn(name, text)

    def test_json_into_a_directory(self):
        import contextlib
        import json
        import tempfile
        from pathlib import Path

        from gemini_res.cli import main

        with tempfile.TemporaryDirectory() as tmp:
            with contextlib.redirect_stdout(io.StringIO()):
                code = main(["-f", "json", "-o", tmp, str(DATA / "900124220465M27LBR.res")])
            self.assertEqual(code, 0)
            written = Path(tmp) / "900124220465M27LBR.json"
            data = json.loads(written.read_text())
            self.assertEqual(len(data["wells"]), 96)
            self.assertEqual(data["plate_id"], "900124220465M27LBR")

    def test_an_unreadable_file_is_reported_and_counted(self):
        import contextlib
        import tempfile
        from pathlib import Path

        from gemini_res.cli import main

        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "not-a-plate.res"
            bad.write_bytes(b"\x00" * 200)
            err = io.StringIO()
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
                code = main([str(bad)])
        self.assertEqual(code, 1)
        self.assertIn("not-a-plate.res", err.getvalue())


if __name__ == "__main__":
    unittest.main()
