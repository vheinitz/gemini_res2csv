"""Write a run the way Gemini's own CSV export writes it.

The instrument's export is configured per assay (``CCSVExportSettings`` sits in
the ``.res`` itself), so no two exports have quite the same columns. What is
written here is the subset every configuration seen contains, in Gemini's own
column order and with its own quoting (all fields quoted, CRLF line ends) — so a
tool that already reads the instrument's files reads these without a special case.

**The quantitative columns are computed** from the four-parameter logistic over
the calibrators; see :mod:`gemini_res.curve`. The value of a well belongs to its
own OD, the mean to the **mean OD** of the duplicate determination; that is how
Gemini does it (checked against run …M27LBR, well CalD: OD mean 0.896 → 26.68, as
in the export). ``*****`` is written where Gemini writes it too: outside the curve
or without a curve.
"""

from __future__ import annotations

import csv
import io

from .reader import Run, calibration_curve

#: Gemini's column order, as its own export writes the header row.
COLUMNS = [
    "Assay", "Plate ID", "Well Location", "Layout Label", "Sample ID",
    "Reader value", "Reader mean", "Quant. 1 value", "Quant. 1 mean",
]


def export_csv(run: Run, curve=None) -> str:
    """The run as CSV text: a header with plate id and assay, then one row per
    used well.

    ``curve`` may be passed in when it has already been fitted; otherwise it is
    fitted here. ``None`` from :func:`gemini_res.reader.calibration_curve` means
    there is no curve, and every quantitative field reads ``*****``.
    """
    if curve is None:
        curve = calibration_curve(run)

    def quant(value):
        """Concentration as text — ``*****`` where there is none."""
        if curve is None or value is None:
            return "*****"
        k = curve.concentration(value)
        return "*****" if k is None else "%.2f" % k

    # **No extra note in the CSV**: since ``CSigmoid::Fit`` is reproduced, the
    # values agree with the instrument's, so the file should look byte for byte
    # like one of its own. What is remarkable about the run is in ``run.gaps``.
    buffer = io.StringIO()
    # The instrument's own layout, byte for byte: two unquoted header lines, two
    # empty ones, the column names **unquoted**, and only then quoted data rows.
    buffer.write("Plate ID,%s\r\nAssay,%s\r\n\r\n\r\n" % (run.plate_id, run.assay_name))
    buffer.write(",".join(COLUMNS) + "\r\n")
    writer = csv.writer(buffer, quoting=csv.QUOTE_ALL, lineterminator="\r\n")
    means: dict[str, float] = {}
    for row in run.samples():
        for name in row["wells"]:
            means[name] = row["od_mean"]
    for row in run.controls():
        for name in row["wells"]:
            means[name] = row["od_mean"]
    for w in run.wells:
        if not w.used:
            continue
        # No layout, no mean: which wells belong together is then not in the
        # file — ``*****`` instead of an invented grouping.
        od_mean = None if w.label == "C?" else means.get(w.name, w.od)
        mean_text = "*****" if od_mean is None else "%.3f" % od_mean
        writer.writerow(
            [run.assay_name, run.plate_id, w.name, w.label, w.sample_id,
             "%.3f" % w.od, mean_text, quant(w.od), quant(od_mean)]
        )
    return buffer.getvalue()
