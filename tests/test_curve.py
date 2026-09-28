"""The four-parameter logistic against the instrument's own quantitative results.

This is the part that had to be reconstructed rather than read: the ``.res`` holds
no concentrations, only optical densities, and Gemini recomputes them every time a
plate is opened. The test therefore asks the only question that matters — do the
numbers come out the same as the instrument's?
"""

from __future__ import annotations

import math
import unittest

from gemini_res import NoCurve, calibration_curve, calibrator_points, curve_from, four_pl, read_run

from .gemini_export import DATA, number, rows

#: Runs with an export whose plate carries a single assay, so the curve of the
#: plate is the curve of every well on it.
COMPARABLE = ["900124220465M27LBR", "900124220465MRTLBR", "900325136490G1RH"]


def run_of(name: str):
    return read_run(DATA / (name + ".res"))


class FourPL(unittest.TestCase):
    def test_shape(self):
        p = (0.05, 1.0, 100.0, 3.0)  # a, b, c, d
        self.assertAlmostEqual(four_pl(0.0, p), 0.05)  # the zero standard is a
        self.assertAlmostEqual(four_pl(100.0, p), (0.05 + 3.0) / 2)  # c is the midpoint
        self.assertGreater(four_pl(10_000.0, p), 2.9)  # d is the upper asymptote
        self.assertLess(four_pl(1.0, p), four_pl(10.0, p))  # monotone

    def test_negative_c_does_not_go_complex(self):
        # (-2.0) ** 0.7 is a complex number in Python; a fit that derails must
        # end in a NaN, not in a value nobody checked.
        self.assertTrue(math.isnan(four_pl(5.0, (0.1, 0.7, -2.0, 3.0))))

    def test_inverse(self):
        curve = curve_from([(0, 0.05), (10, 0.4), (30, 0.9), (100, 2.0), (300, 2.8)])
        for concentration in (5.0, 25.0, 120.0):
            od = four_pl(concentration, curve.parameters)
            self.assertAlmostEqual(curve.concentration(od), concentration, places=4)

    def test_outside_the_curve(self):
        curve = curve_from([(0, 0.05), (10, 0.4), (30, 0.9), (100, 2.0), (300, 2.8)])
        self.assertEqual(curve.concentration(0.0), 0.0)  # at or below the bottom
        self.assertIsNone(curve.concentration(9.9))  # above the upper asymptote


class Refusals(unittest.TestCase):
    def test_too_few_points(self):
        with self.assertRaises(NoCurve):
            curve_from([(0, 0.05), (10, 0.4), (30, 0.9)])

    def test_only_the_zero_standard(self):
        with self.assertRaises(NoCurve):
            curve_from([(0, 0.05), (0, 0.06), (0, 0.05), (0, 0.04)])

    def test_a_split_plate_gets_no_curve(self):
        # Two assays, one layout: the second half would be measured against the
        # first half's calibrators. No number is better than that number.
        run = run_of("900126350494GM03LBR3")
        self.assertIsNone(calibration_curve(run))
        self.assertTrue(any("split plate" in gap for gap in run.gaps))


class AgainstTheInstrument(unittest.TestCase):
    """Every exported concentration, recomputed from the optical densities."""

    def deviations(self, name: str) -> list[tuple[float, float, str]]:
        """(relative deviation, absolute deviation, well) per exported value."""
        run = run_of(name)
        curve = calibration_curve(run)
        self.assertIsNotNone(curve, name)
        out = []
        for row in rows(name):
            expected = number(row["Quant. 1 value"])
            if number(row["Reader value"]) is None or expected is None or expected <= 0:
                continue
            # The **raw** OD out of the file, not the three decimals of the
            # export: just above the lower asymptote the curve is so steep in
            # concentration that a thousandth of OD is worth several per cent,
            # and the instrument computed from the raw value as well.
            od = run.well(row["Well Location"]).od
            got = curve.concentration(od)
            self.assertIsNotNone(got, "%s %s" % (name, row["Well Location"]))
            out.append((abs(got - expected) / expected, abs(got - expected),
                        row["Well Location"]))
        self.assertGreater(len(out), 10, name)
        return sorted(out)

    def test_median_deviation_is_below_one_per_mille(self):
        for name in COMPARABLE:
            with self.subTest(name):
                deviations = self.deviations(name)
                median = deviations[len(deviations) // 2][0]
                self.assertLess(median, 0.001, "median %.5f" % median)

    def test_no_single_value_is_off_by_more_than_the_export_can_show(self):
        """Every exported value, against the precision the export has.

        The export writes two decimals, so a value of 0.03 carries ±0.005 of
        quantisation on its own — 17 % in relative terms, and nothing to do with
        the fit. The yardstick is therefore half of the last exported digit plus
        one per mille of the value itself.

        **Just above the lower asymptote that is not enough**, and the reason is
        the curve, not the code: in run 900325136490G1RH the fit puts ``a`` at
        0.05866, and well C4 reads an OD of 0.061 — two thousandths above it. The
        inverse of the logistic has no bound there, so the fifth decimal of ``a``
        decides whether the result reads 0.02 or 0.03. Below 0.05 the tolerance is
        one full exported digit; a run whose values live down there is flagged as
        unsaturated anyway.
        """
        for name in COMPARABLE:
            with self.subTest(name):
                for relative, absolute, well in self.deviations(name):
                    value = absolute / relative if relative else 0.0
                    allowed = 0.01 if value <= 0.05 else 0.005 + 0.001 * value
                    self.assertLessEqual(
                        absolute, allowed,
                        "%s %s: %.4f off, %.4f allowed" % (name, well, absolute, allowed),
                    )

    def test_the_mean_belongs_to_the_mean_od(self):
        # Gemini does not average the two concentrations; it computes one
        # concentration from the averaged OD. CalD of this run is the example:
        # 34.65 and 19.74 average to 27.20, but the export says 26.68.
        run = run_of("900124220465M27LBR")
        curve = calibration_curve(run)
        means = {}
        for entry in run.samples() + run.controls():
            for well in entry["wells"]:
                means[well] = entry["od_mean"]
        for row in rows("900124220465M27LBR"):
            expected = number(row["Quant. 1 mean"])
            if expected is None or expected <= 0:
                continue
            got = curve.concentration(means[row["Well Location"]])
            self.assertLess(abs(got - expected) / expected, 0.01, row["Well Location"])


class Saturation(unittest.TestCase):
    def test_a_saturated_series_is_well_determined(self):
        run = run_of("900124220465M27LBR")
        self.assertTrue(calibration_curve(run).well_determined)

    def test_iterations_matter_only_where_the_fit_still_moves(self):
        # CSigmoid::Fit runs a fixed number of iterations. Where it has settled,
        # more of them change nothing — that is what makes 100 a safe choice.
        points = calibrator_points(run_of("900124220465M27LBR"))
        a = curve_from(points, iterations=100).parameters
        b = curve_from(points, iterations=200).parameters
        for x, y in zip(a, b):
            self.assertAlmostEqual(x, y, places=6)


if __name__ == "__main__":
    unittest.main()
