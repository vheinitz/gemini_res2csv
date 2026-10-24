# gemini-res

Reads the `.res` result files of a Stratec Gemini ELISA processor and exports
them as CSV (in the instrument's own export layout), JSON or a short summary,
without Windows and without the instrument software. Python standard library
only.

```console
$ gemini-res --format summary 900325136490G1RH.res
900325136490G1RH.res  900325136490G1RH
  assay        9003_a-TPO_IgG_V3_QC
  run          2026-09-15 10:12
  user         QC User D2
  temperature  24.47 °C
  wells used   32 of 96, 16 with a sample
  samples      8
  calibrators  0, 30, 100, 300, 1000, 3000 IU/ml
  4PL          a=0.0587 b=0.5996 c=66606.3582 d=16.8322
  reagents     14, no lot recorded
  ! calibration curve not saturated: highest standard at 14 % of the upper asymptote
```

A `.res` file holds optical densities, plate layout, sample ids, reagent lots
and the run's event log, but no concentrations. This package recomputes them
with a four-parameter logistic fitted to the calibrators on the plate (details
in [`docs/FORMAT.md`](docs/FORMAT.md)).

Private spare-time project. Not affiliated with or endorsed by Stratec, not
validated for diagnostic use.

## Install

```console
pip install .
```

Or copy the `gemini_res/` directory into your project. Requires Python 3.9 or
newer (`pyproject.toml`); the tests were run on CPython 3.12.

## Command line

```console
gemini-res FILE...                      # CSV on stdout (default)
gemini-res --format json FILE           # everything the reader found
gemini-res --format summary runs/       # summary per run
gemini-res --format csv -o out/ runs/   # one file per run into out/
```

A directory argument is searched (not recursively) for `*.res`. Unreadable
files are reported on stderr; the exit status is 1 if any file failed.
`python -m gemini_res` works without installing, from the repository root.

## Library

```python
from gemini_res import read_run, export_csv, calibration_curve

run = read_run("900325136490G1RH.res")
run.plate_id          # '900325136490G1RH'
run.assay_name        # '9003_a-TPO_IgG_V3_QC'
run.timestamp         # datetime(2026, 9, 15, 10, 12, 29)
run.unit              # 'IU/ml'
run.reagents          # [Reagent(name='9003_a-TPO_IgG_V3_QC', lot=''), ...]
run.gaps              # what the reader could not determine, as sentences

for sample in run.samples():
    sample["sample_id"], sample["wells"], sample["od"], sample["od_mean"]
    # 'S100000000', ['A3', 'B3'], [1.009, 1.009], 1.009
    # sample["od_excluded"]: ODs of wells the instrument flagged

curve = calibration_curve(run)   # None if no curve can be fitted (see run.gaps)
curve.concentration(1.009)       # 611.59 (IU/ml)
curve.parameters                 # (a, b, c, d)

export_csv(run)                  # CSV text in the instrument's layout
run.as_dict()                    # for json.dumps
```

## Tests

```console
python -m unittest discover -t .
```

53 tests (one is skipped unless the local anonymisation list
`tools/literals.local.tsv` exists). `tests/data` holds five anonymised runs;
four of them come with the CSV export the instrument wrote for the same plate,
and the tests compare against those exports:

- well ODs, layout labels and sample ids (within 0.0011 OD, i.e. the three
  decimals of the export),
- one CSV row per used well, in the instrument's order; the CSV head is
  byte-identical for two of the runs,
- concentrations per well in the three single-assay runs with export: the
  test requires a median relative deviation below 1 ‰ (currently 0.06–0.09 ‰),
  and every value within the rounding of the export plus 1 ‰,
- for one run, the duplicate mean computed from the mean OD, within 1 %.

## Limitations

The reader reports these in `run.gaps` and writes `*****` (the instrument's
"no value" marker) in the CSV instead of guessing:

- No qualitative result (positive/borderline/negative): the schema of
  `CQualitativeSettings` is not worked out.
- No concentrations for split plates (two assays on one plate): only the
  layout of the first assay is read.
- Hand-pipetted plates contain no dilution groups; the control wells are
  reported as used but unnamed (`C?`).
- The pipetting protocol (step classes) is not read.

## File format

[`docs/FORMAT.md`](docs/FORMAT.md) describes the format field by field (MFC
`CArchive` conventions, header, wells, layout maps, OD matrix, calibrators,
reagent lots, event log), the 4PL fit as the instrument does it, and what is
still unexplained.

## Sample files and anonymisation

The runs in `tests/data` come from a real laboratory. Operator and user names,
the vendor token, catalogue numbers and the stems of sample ids were replaced
with strings of the same byte length, because a `.res` is a serialised object
graph without an index; all numbers are unchanged. Dilution suffixes in sample
ids (`1:200`, `-1280`, `/400`) were kept.

The script used for this:

```console
python tools/anonymise.py run.res out_dir/
```

Sample ids are found in the file. Names and other literals are not: list them
in `tools/literals.local.tsv`, one `original<TAB>placeholder` per line, both of
the same byte length. The file is ignored by git.

Useful contributions: anonymised files that make the reader fail
(`ResFormatError`), a qualitative assay with its export, a split plate with its
export.

## Licence

MIT, see [LICENSE](LICENSE).
