# gemini-res

Read the result files of a **Stratec Gemini** ELISA processor — `*.res` — and turn
them into CSV, without Windows and without the instrument software.

```console
$ gemini-res plate.res > plate.csv
$ gemini-res --format summary 340125136490G1RH.res
340125136490G1RH.res  340125136490G1RH
  assay        3401_a-TPO_IgG_V3_QC
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

Pure Python, standard library only, no dependencies.

## Why

A Gemini writes every plate it has run into a `.res` file and can export a CSV
from it — on the instrument's PC, under Windows, by hand, with columns that are
configured per assay. Any pipeline that wants the results somewhere else ends up
depending on that manual step.

The `.res` file itself holds everything except the results: optical densities,
plate layout, sample ids, reagent lots, the run's event log. The concentrations
are recomputed by the instrument each time a plate is opened, which is why they
are not in the file — so this package recomputes them the same way, from the
calibrators on the plate.

That makes the `.res` the one reliable source, and reading it directly means a
laboratory can get its results out of a Gemini on any machine, automatically.

## Install

```console
pip install .
```

or simply copy the `gemini_res/` directory next to your own code — it imports
nothing but the standard library. Python 3.9 or newer (developed and tested on
CPython 3.12).

## Use it from the command line

```console
gemini-res FILE...                     # Gemini-style CSV on stdout
gemini-res --format json FILE          # everything the reader found
gemini-res --format summary runs/       # a dozen lines per run
gemini-res --format csv -o out/ runs/   # one file per run into out/
```

A directory argument is searched for `*.res`. Files that cannot be read are
reported on stderr and counted; the exit status is 1 if any of them failed, so the
command can carry a pipeline.

`python -m gemini_res` does the same thing if you have not installed the package.

## Use it as a library

```python
from gemini_res import read_run, export_csv, calibration_curve

run = read_run("340125136490G1RH.res")

run.plate_id          # '340125136490G1RH'
run.assay_name        # '3401_a-TPO_IgG_V3_QC'
run.timestamp         # datetime(2026, 9, 15, 10, 12, 29)
run.unit              # 'IU/ml'
run.reagents          # [Reagent(name='3401_a-TPO_IgG_V3_QC', lot=''), ...]
run.gaps              # what the reader could not read — see below

for sample in run.samples():
    sample["sample_id"]   # 'S100000000'
    sample["wells"]       # ['A3', 'B3']
    sample["od"]          # [1.009, 1.009]  — the values that count
    sample["od_excluded"] # ODs of wells the instrument flagged
    sample["od_mean"]     # 1.009

curve = calibration_curve(run)   # or None, and run.gaps says why
curve.concentration(1.009)       # 611.59  IU/ml
curve.parameters                 # (a, b, c, d) of the four-parameter logistic

print(export_csv(run))           # the instrument's own CSV layout
run.as_dict()                    # everything, ready for json.dumps
```

## How far you can trust it

Every claim in this package is checked against the instrument's own export of the
same plate. `tests/data` holds five runs with the exports the laboratory took from
the Gemini at the time, and the test suite compares against them:

| checked against the instrument | result |
|---|---|
| optical density of every exported well | equal to the three decimals the export shows |
| plate layout: `CalA`…`CalF`, `PC1`, `NC1`, `CO1` | equal |
| sample ids and the pairing of duplicates | equal |
| mean per duplicate, flagged wells excluded | equal |
| concentration per well and per duplicate | median 0.006…0.05 ‰ off |
| the CSV head, byte for byte | equal |

```console
python -m unittest discover -t .
```

**What it will not do**, and says so rather than guessing:

* **No qualitative result.** Positive / borderline / negative comes out of
  `CQualitativeSettings`, whose schema is not worked out.
* **No quantitative results for a split plate.** Two assays on one plate share
  the plate id but have their own calibrators in their own half; the reader only
  has the layout of the first, so it reports the fact instead of applying the
  wrong curve to the second half.
* **No control layout for a plate pipetted by hand.** Such a file contains no
  dilution groups at all. The wells are reported as used and unnamed (`C?`).
* **No pipetting protocol.** The step classes are skipped entirely.

Anything of that kind ends up in `run.gaps` as a sentence, and in the CSV as
`*****` — the same marker the instrument uses for "no value".

## The file format

[`docs/FORMAT.md`](docs/FORMAT.md) documents the format field by field: the MFC
`CArchive` conventions it is built on, the header, the well records, the layout
maps, the density matrix, the calibrators, the reagent lots, the event log — and
the four-parameter logistic the instrument recomputes its results with, including
the details of the Levenberg-Marquardt loop that decide whether the numbers come
out the same.

It also lists what is still unexplained, so that the next person starts where this
left off rather than at the beginning.

## The sample files

The five runs in `tests/data` come from a real laboratory and were anonymised
before publication: operator, users, the vendor token and the stem of every sample
identifier were replaced. Because a `.res` is a serialised object graph with no
index, the replacement had to be **byte for byte of the same length** — every
number in those files is untouched, which is what makes them usable as a
reference.

`tools/anonymise.py` is the script that did it; run it on your own files before
sharing them:

```console
python tools/anonymise.py run.res out_dir/
```

Sample identifiers are found in the file itself. Names of operators, users and
your company are not: list them in `tools/literals.local.tsv`, one
`original<TAB>placeholder` per line, both of the same length. That file is
ignored by git — committed, it would publish the very names it removes.

The trailing dilutions in the sample ids (`1:200`, `-1280`, `/400`) were kept on
purpose: they are how laboratories encode a dilution in a sample id, and anyone
importing Gemini results will have to deal with them.

## Contributing

What would help most, in this order:

1. **Files that break the reader.** It fails loudly with `ResFormatError` rather
   than inventing a number, so a stack trace plus an anonymised file is a
   complete bug report.
2. **A qualitative assay with its export**, to work out `CQualitativeSettings`.
3. **A split plate**, to get the second assay's layout out of the archive instead
   of refusing the plate.

Please anonymise before you attach anything, and keep the instrument's own CSV
export with the file — without it there is nothing to check against.

## Licence

MIT, see [LICENSE](LICENSE).

This project is not affiliated with, endorsed by or supported by Stratec. "Gemini"
is used only to name the instrument whose files these are.
