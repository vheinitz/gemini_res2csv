"""Read Stratec Gemini ``.res`` result files and export them as CSV.

    from gemini_res import read_run, export_csv

    run = read_run("plate.res")
    print(export_csv(run))

No Windows, no instrument software, no dependencies beyond the standard library.
What the file format looks like is documented in ``docs/FORMAT.md``; what the
quantitative arithmetic is, in :mod:`gemini_res.curve`.
"""

from .csv_export import export_csv
from .curve import Curve, NoCurve, curve_from, four_pl
from .reader import (
    Archive,
    Event,
    Reagent,
    ResFormatError,
    Run,
    Well,
    calibration_curve,
    calibrator_points,
    ole_date,
    read_run,
    split_plate,
    well_name,
)

__all__ = [
    "Archive",
    "Curve",
    "Event",
    "NoCurve",
    "Reagent",
    "ResFormatError",
    "Run",
    "Well",
    "calibration_curve",
    "calibrator_points",
    "curve_from",
    "export_csv",
    "four_pl",
    "ole_date",
    "read_run",
    "split_plate",
    "well_name",
]

__version__ = "0.1.0"
