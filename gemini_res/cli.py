"""Command line: turn ``.res`` files into CSV, JSON or a short summary.

    python -m gemini_res plate.res                 # Gemini-style CSV on stdout
    python -m gemini_res --format summary *.res    # one block per run
    python -m gemini_res --format json -o out/ runs/   # one .json per file

A directory argument is searched for ``*.res`` (case-insensitive, not recursive).
Files that cannot be read are reported on stderr and counted; the exit status is
1 if any file failed, so the command can be used in a pipeline.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .csv_export import export_csv
from .reader import ResFormatError, calibration_curve, read_run

SUFFIX = {"csv": ".csv", "json": ".json", "summary": ".txt"}


def _files(paths: list[str]) -> list[Path]:
    found: list[Path] = []
    for raw in paths:
        p = Path(raw)
        if p.is_dir():
            found.extend(sorted(q for q in p.iterdir() if q.suffix.lower() == ".res"))
        else:
            found.append(p)
    return found


def summary(run) -> str:
    """The run in a dozen lines — for a first look at a file."""
    curve = calibration_curve(run)
    lines = [
        "%s  %s" % (run.file, run.plate_id),
        "  assay        %s" % (run.assay or "?"),
        "  run          %s"
        % run.timestamp.strftime("%Y-%m-%d %H:%M"),
        "  user         %s" % (run.user or "?"),
        "  temperature  %s °C" % run.temperature,
        "  wells used   %d of 96, %d with a sample"
        % (sum(1 for w in run.wells if w.used), sum(1 for w in run.wells if w.is_sample)),
        "  samples      %d" % len(run.samples()),
        "  calibrators  %s %s"
        % (", ".join("%g" % c for c in run.calibrator_concentrations) or "—", run.unit),
    ]
    if curve is not None:
        a, b, c, d = curve.parameters
        lines.append("  4PL          a=%.4f b=%.4f c=%.4f d=%.4f" % (a, b, c, d))
    # Only the lots, and only when there are any: the list holds a dozen names
    # per run, and a laboratory that records no lots would get a dozen dashes.
    lots = [r for r in run.reagents if r.lot]
    lines.append(
        "  reagents     %d, %s"
        % (len(run.reagents),
           ", ".join("%s lot %s" % (r.name, r.lot) for r in lots) if lots
           else "no lot recorded")
    )
    for gap in run.gaps:
        lines.append("  ! %s" % gap)
    return "\n".join(lines) + "\n"


def render(run, fmt: str) -> str:
    if fmt == "csv":
        return export_csv(run)
    if fmt == "json":
        return json.dumps(run.as_dict(), indent=2, ensure_ascii=False) + "\n"
    return summary(run)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="gemini-res",
        description="Read Stratec Gemini .res files without the instrument software.",
    )
    parser.add_argument("paths", nargs="+", metavar="FILE", help=".res file or directory")
    parser.add_argument(
        "--format", "-f", choices=sorted(SUFFIX), default="csv",
        help="csv (Gemini's own export format, the default), json (everything the "
             "reader found) or summary (a dozen lines per run)",
    )
    parser.add_argument(
        "--out", "-o", metavar="DIR",
        help="write one file per run into this directory instead of stdout",
    )
    args = parser.parse_args(argv)

    files = _files(args.paths)
    if not files:
        print("no .res file found", file=sys.stderr)
        return 1
    out = Path(args.out) if args.out else None
    if out is not None:
        out.mkdir(parents=True, exist_ok=True)

    failed = 0
    for path in files:
        try:
            run = read_run(path)
            text = render(run, args.format)
        except (ResFormatError, OSError) as error:
            # Keep going: a batch of a hundred plates should not stop at one
            # file, and which one failed has to be visible afterwards.
            print("%s: %s" % (path.name, error), file=sys.stderr)
            failed += 1
            continue
        if out is None:
            sys.stdout.write(text)
        else:
            target = out / (path.stem + SUFFIX[args.format])
            target.write_text(text, encoding="utf-8", newline="")
            print("%s -> %s" % (path.name, target))
    if failed:
        print("%d of %d files could not be read" % (failed, len(files)), file=sys.stderr)
    return 1 if failed else 0
