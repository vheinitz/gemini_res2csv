"""The anonymiser: whoever publishes their own files depends on these rules."""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

from gemini_res import read_run

from .gemini_export import DATA

_spec = importlib.util.spec_from_file_location(
    "anonymise", Path(__file__).resolve().parent.parent / "tools" / "anonymise.py"
)
anonymise_tool = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(anonymise_tool)


class Mapping(unittest.TestCase):
    def test_every_replacement_keeps_its_length(self):
        table = anonymise_tool.mapping_for(
            ["4711", "4711-200", "A very long sample identifier 1:160PP", "x"]
        )
        for before, after in table.items():
            self.assertEqual(len(before), len(after), before)

    def test_the_dilution_survives(self):
        table = anonymise_tool.mapping_for(["4711-200", "0815 1:160PP", "22/400"])
        self.assertTrue(table[b"4711-200"].endswith(b"-200"))
        self.assertTrue(table[b"0815 1:160PP"].endswith(b" 1:160PP"))
        self.assertTrue(table[b"22/400"].endswith(b"/400"))

    def test_two_identifiers_never_become_one(self):
        # A truncated placeholder could collide; the tool has to refuse rather
        # than merge two samples into one.
        many = ["%02d" % n for n in range(60)]
        try:
            table = anonymise_tool.mapping_for(many)
        except ValueError as error:
            self.assertIn("would both become", str(error))
        else:
            self.assertEqual(len(set(table.values())), len(table))


class OnePass(unittest.TestCase):
    def test_a_replacement_is_not_replaced_again(self):
        # The bug this guards against: replacing "4711" first and "200" after it
        # eats the dilution of "4711-200" and turns it into "S1-S2".
        table = anonymise_tool.mapping_for(["4711-200", "200"])
        out = anonymise_tool.anonymise(b"id=4711-200;other=200", table)
        self.assertEqual(out, b"id=" + table[b"4711-200"] + b";other=" + table[b"200"])
        self.assertIn(b"-200", out)

    def test_length_is_preserved(self):
        raw = (DATA / "320424220465M27LBR.res").read_bytes()
        run = read_run(DATA / "320424220465M27LBR.res")
        table = anonymise_tool.mapping_for([w.sample_id for w in run.wells])
        self.assertEqual(len(anonymise_tool.anonymise(raw, table)), len(raw))


class AlreadyClean(unittest.TestCase):
    """The published files must not still carry what was to be removed."""

    def test_no_plain_names_left(self):
        # The names are only on the machine that anonymised the files.
        if not anonymise_tool.LITERALS:
            self.skipTest("no %s here" % anonymise_tool.LITERALS_FILE.name)
        for path in sorted(DATA.iterdir()):
            with self.subTest(path.name):
                raw = path.read_bytes()
                for gone in anonymise_tool.LITERALS:
                    self.assertNotIn(gone, raw)

    def test_running_it_again_renumbers_but_keeps_the_numbers(self):
        # The placeholders are valid sample ids themselves, so a second pass
        # renumbers them — S1000 may become S3000. That is harmless as long as no
        # measurement moves, which is the property the files are published for.
        for name in ("320424220465M27LBR", "340125136490G1RH"):
            with self.subTest(name):
                path = DATA / (name + ".res")
                raw = path.read_bytes()
                run = read_run(path)
                table = anonymise_tool.mapping_for([w.sample_id for w in run.wells])
                again = anonymise_tool.anonymise(raw, table)
                self.assertEqual(len(again), len(raw))
                scratch = DATA.parent / "twice.res"
                scratch.write_bytes(again)
                try:
                    self.assertEqual(
                        [w.od for w in read_run(scratch).wells], [w.od for w in run.wells]
                    )
                finally:
                    scratch.unlink()


if __name__ == "__main__":
    unittest.main()
