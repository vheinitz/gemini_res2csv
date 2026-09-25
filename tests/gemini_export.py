"""Read the instrument's own CSV export — the yardstick for every test here.

The tests do not check the reader against expectations written down by hand; they
check it against what Gemini itself wrote for the same plate. Each ``.res`` in
``tests/data`` is accompanied by the export the laboratory took from the
instrument at the time.

Two properties of those exports have to be dealt with here: the numbers follow
the Windows locale of the machine that wrote them (one file uses a comma as the
decimal separator), and ``*****`` stands for "no value" — outside the calibration
curve, or no curve at all.
"""

from __future__ import annotations

import csv
from pathlib import Path

DATA = Path(__file__).resolve().parent / "data"


def number(text: str):
    """A number from the export, or ``None`` for ``*****`` and empty fields."""
    text = text.strip()
    if not text or set(text) == {"*"}:
        return None
    return float(text.replace(",", "."))


def rows(name: str) -> list[dict]:
    """The well rows of one export, in the order the instrument wrote them."""
    text = (DATA / (name + ".csv")).read_text(encoding="cp1252")
    lines = text.splitlines()
    head = next(i for i, line in enumerate(lines) if line.startswith("Assay,Plate ID,"))
    return list(csv.DictReader(lines[head:]))


def has_export(name: str) -> bool:
    return (DATA / (name + ".csv")).exists()


#: Every run in ``tests/data``, and what makes each of them worth keeping.
RUNS = {
    "320424220465M27LBR": "3204 Cardiolipin IgM at 27 °C, with export",
    "320424220465MRTLBR": "the same assay, version 2, with export",
    "320426350494GM03LBR3": "two assays on one plate (IgG left, IgM right)",
    "320625480manGM5aLKE": "pipetted by hand — no layout in the file",
    "340125136490G1RH": "3401 a-TPO, export written with a decimal comma",
}
