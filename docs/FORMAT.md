# The Gemini `.res` file format

A Stratec Gemini stores every plate it has run as one `.res` file. This document
describes what those bytes are, field by field, as far as they have been worked
out — and says plainly where they have not.

Nothing here comes from a specification. It was reconstructed from files that
came with the instrument's own CSV export of the same plate, so that every claim
could be checked against a number the instrument itself had written, plus a read
of `DataAnalysis.dll` for the arithmetic that the file does not contain. Where a
field is still unexplained, it says so; where a schema is checked against several
files, it says that too.

* [1. What kind of file it is](#1-what-kind-of-file-it-is)
* [2. The header](#2-the-header)
* [3. The wells](#3-the-wells)
* [4. The plate layout](#4-the-plate-layout)
* [5. The optical densities](#5-the-optical-densities)
* [6. Assay, calibrators, unit](#6-assay-calibrators-unit)
* [7. Reagent lots](#7-reagent-lots)
* [8. Plate events](#8-plate-events)
* [9. What is *not* in the file: the results](#9-what-is-not-in-the-file-the-results)
* [10. Split plates](#10-split-plates)
* [11. Classes in the file](#11-classes-in-the-file)
* [12. What is still unexplained](#12-what-is-still-unexplained)

## 1. What kind of file it is

An MFC `CArchive` serialisation. Gemini.exe is an MFC application and stores its
object graph with the library's own operators, so the file follows those rules
rather than a format of its own. There is no magic number and no index: the
first four bytes are already payload.

Everything is little-endian.

**Strings** are `CString`: one length byte, then the bytes, encoded in the
Windows code page (cp1252 in every file seen). A length of `0xFF` means the real
length follows as a WORD; a WORD of `0xFFFF` means it follows as a DWORD. So a
16-character plate id is `10 33 34 30 ...`.

**Objects** start with a WORD tag:

| tag | meaning |
|---|---|
| `0xFFFF` | a class named here for the first time: WORD schema, WORD name length, then the name in ASCII |
| `0x8000 \| n` | an object of a class already named; `n` is its index in the archive's table |
| `n` (`< 0x8000`) | a reference to an object already written |

The index `n` counts classes **and** objects, which is why the numbers grow
large in a long file. The consequence matters for reading: **a class name appears
only once**, however many objects of it the file holds. `CQuantitativeSettings`
occurs exactly once in the raw bytes of a plate that carries two assays with two
settings objects. Counting names is therefore not a way to count objects — see
[§10](#10-split-plates).

**Arrays** (`CObArray`) write their element count as a WORD in front of the
elements: `60 00` = 96 wells.

### Why this reader is anchored, not sequential

Reading a `CArchive` from beginning to end requires knowing the schema of every
class in it — here, some forty, most of which describe pipetting, washing and
report layout. This reader instead reads the header and the well array
sequentially, where every byte is accounted for, and otherwise jumps to the
offsets where the archive itself names the classes it needs. Each of those
schemas is checked against several files and raises `ResFormatError` when a byte
does not fit.

## 2. The header

The file begins with it, at offset 0:

```
11 00 00 00                          i32   17    version; 17 in every file seen
00                                   u8          0 in every file seen
54 81 59 9c 0d 99 e6 40              double      OLE date: when the plate was saved
10 "900325136490G1RH"                CString     plate id (the plate's barcode)
0a "QC User D2"                      CString     the user who saved the run
00 00 00 00                          i32   0     unexplained
08 00 00 00                          i32   8     rows
0c 00 00 00                          i32   12    columns
b8 1e 85 eb 51 78 38 40              double      temperature, 24.47 °C
```

**The OLE date** is the usual automation date: days since 1899-12-30 as a
double, fraction for the time of day.

**The temperature** is the instrument's, not the assay's. A plate running an
assay named `..._27C` records 22.43 °C — the room, or the reader. The incubation
temperature the assay asks for is part of the assay, not of this field.

The plate geometry is checked (8 × 12): everything downstream assumes 96 wells.

## 3. The wells

`CWell`, 96 records, preceded by the WORD count. **Column-major order**: index 0
is A1, 1 is B1, 8 is A2, 95 is H12. That is how Gemini fills a plate, and the
row order of its own export confirms it.

One record, 31 bytes plus the strings — here an empty calibrator well:

```
0b                        u8    11     record kind; 11 in every well seen
00                        u8           result flag (see below)
00 00 00 00               i32          occupancy: -1 = unused, otherwise 0 or 1
ff ff ff ff               i32   -1     sample index, -1 = no sample record
00 00 00 00               i32          unexplained (0 here, 64 at a sample well)
00                        u8           unexplained
00                        CString      rack position, empty here
00                        CString      sample id, empty here
00 00 00                  3 bytes      unexplained
01                        u8    1
ff ff ff ff               i32   -1
01 00 00 00               i32   1
00                        u8
00                        CString      the sample id a second time
```

and the same record for a well holding a sample:

```
0b 00  00 00 00 00  00 00 00 00  40 00 00 00  00
04 "12.1"  0a "S100000000"  00 00 00  01  ff ff ff ff  01 00 00 00  00
0a "S100000000"
```

**The sample index** ties the two wells of a duplicate determination together:
both wells of one sample carry the same index, and it is the index into the
run's sample list, not a position on the plate.

**The rack position** (`12.1`) is where the tube stood in the sample rack.

**The result flag** is the one field in this record that changes a number. A
flagged well is reported but **does not count towards the mean**: in run
`900124220465MRTLBR` well B4 is flagged, its own OD is 0.027, A4 reads 0.079, and
the instrument's export gives both wells a mean of 0.079 — A4 alone. It is the
only flagged well in the sample files, and it is exactly the well the export
excludes.

**Unused wells** carry `-1` as occupancy. They hold 0.0 in the density matrix, and
that zero is not a measurement.

## 4. The plate layout

Which well is a calibrator, which a control and which a sample is not encoded in
the well record. It is in the dilution groups, `CDilutionGroup`: each group
carries its name and then a map of the plate.

Behind the name follow `08 00 00 00`, `0c 00 00 00` (the 8 × 12 geometry again), a
WORD, the count `60 00` = 96, and then **96 bytes**, one per well in column-major
order: 1 where the well belongs to the group.

Group names look like `CAL_A_Cardiolipin-GM`, `PC_…`, `NC_…`, `CO_…` and
`Sample`. The reader turns them into the labels the instrument's export uses:
`CalA`…`CalF`, `PC1`, `NC1`, `CO1`, and `T1`, `T2`, … for samples, numbered by
their sample index.

**Without a pipetting protocol there is no layout.** Plates pipetted by hand (the
sample file `900225480manGM5aLKE`) contain no dilution groups at all. Their
control wells are used and carry no sample, and that is all the file says; the
reader labels them `C?`, reports each on its own, and records the fact as a gap
rather than inventing a grid.

## 5. The optical densities

`CSetOfResults`, one 8 × 12 matrix of doubles, row-major:

```
<class tag>  u8  i32 8 (rows)  i32 12 (columns)  96 × double
```

These are the raw reader values. The instrument's export rounds them to three
decimals; the file does not. Means are computed from the raw values and rounded
only for display — rounding first is off by a thousandth at values like 1.2215.

A plate can contain more than one such matrix (kinetic reads, blanks). The reader
takes the first, which in every file examined is the endpoint read the export
reports.

## 6. Assay, calibrators, unit

`CAssayHeader`:

```
<class tag>  8 bytes flags  double (created)  i32  double (last changed)
CString assay  CString author  …  CString plate type  CString description
```

**Two assay names, and they can differ.** This header carries the name of the
assay *definition*; the plate also names the `.asy` *file* it ran, in
`CAssayDetails`, as a full Windows path. Save a copy of an assay under a new file
name and the definition keeps its old name: in `900124220465M27LBR` the header
says `9001_Cardiolipin-GM_IgM_V2_QC` while the file — and the instrument's export
— say `9001_Cardiolipin-GM_IgM_27C`. The file name is the one a laboratory reads
off its result sheet.

`CQuantitativeSettings` holds the unit (`U/ml`, `MPL/ml`) and the calibrator
concentrations: a u8 count followed by that many doubles, ascending, the first of
them 0.0 for the zero standard. The bytes in front of it describe report columns
with fonts and are not worked out, so the reader locates the concentrations by
that shape and takes the unit from the `CString` immediately before it.

The same class also holds the **number of iterations** of the curve fit (100 in
every assay examined; see [§9](#9-what-is-not-in-the-file-the-results)).

## 7. Reagent lots

`CBatchInformation`, one record per reagent:

```
u8 8  CString lot  CString name  16 bytes
```

The first record follows the class name, each further one its own `0x8000|n` tag.
A lot of `.` or `..` means "not recorded" — none of the sample files carries a real
lot number, because that laboratory did not enter them. When they are entered this
is the only place in the file where the lots of conjugate, substrate and
calibrator appear.

**The first record of this list is not a reagent**: it carries the assay, and it
carries the name of the assay *file* (`9001_Cardiolipin-GM_IgM_27C`), not the
definition's name from `CAssayHeader`. It is therefore a second source for the
name the export shows — see [§6](#6-assay-calibrators-unit).

## 8. Plate events

`CPlateEvent`, repeated:

```
i32 1  u8  double (OLE date)  CString text  i32 number  u8
```

The events are the run's log — when the plate was loaded, incubated, washed,
read. The first and the last event are the run's start and end. A date outside
1982…2119 marks the end of the sequence: past the last event, the next bytes
belong to another class.

## 9. What is *not* in the file: the results

**There is no concentration in a `.res` file.** The instrument recomputes it every
time the plate is opened; the IFU says so (p. 4-67). A reader that wants U/ml has
to do the same arithmetic.

That arithmetic is in `DataAnalysis.dll`, class `CSigmoid`, and it is a
**four-parameter logistic**:

```
OD(x) = d + (a - d) / (1 + (x / c)^b)
```

fitted by Levenberg-Marquardt. What the machine code says, method by method:

| method | what follows from it |
|---|---|
| `CalculateY` | the formula above, parameter order a, b, c, d |
| `InitialiseParameters` | `a = y[0]`, `b = 1`, `c = (x[0] + x[n-1]) / 2`, `d = y[n-1]` |
| `CalculatePartialDerivatives` | the derivatives analytically, not numerically |
| `CheckParameters` | if `c <= 0` then `c = c_previous / 2`; no other bound |
| `Fit` | `mrqmin` from *Numerical Recipes* — the helper is called `mrqcof`, the solver is `CMatrix::GaussJordan` |
| `CDataModel::Sort` | the points are sorted before fitting |

and three details out of `Fit` itself that decide whether the numbers come out
the same:

* initial λ = 0.001, times 0.1 on an improvement, times 10 otherwise;
* the diagonal is **multiplied**, `alpha[j][j] * (1 + λ)`, not incremented;
* a **fixed number of iterations** runs, and every iteration counts, including
  one that is rejected. The convergence test only sets a flag for the return
  value; it does not leave the loop.

The calibrator wells go into the fit **one by one**, not as means. A flagged
calibrator is left out, the same rule the means follow.

Each well's concentration is the curve inverted at its **own** OD; the mean
concentration of a duplicate is the curve inverted at the **mean OD** — not the
average of the two concentrations. Example from `900124220465M27LBR`, well CalD:
34.65 and 19.74 average to 27.20, while the instrument reports 26.68, which is
the curve at OD 0.896.

Reproduced this way, the values agree with the instrument's export to a median of
0.006…0.05 ‰, and on two of the sample runs the four parameters come out
identical to four decimals. Per mille, not per cent: the arithmetic is
reproduced, not approximated.

Where a value lies outside the curve, Gemini writes `*****`, and so does this
package. Just above the lower asymptote the inverse is unbounded, so a value of
0.02 there is not a measurement — the fifth decimal of `a` decides whether it
reads 0.02 or 0.03.

## 10. Split plates

A plate can carry **two assays**, each on its half of the columns and each with
its own calibrators. In one laboratory's archive, 21 of 98 exported runs were of
that kind (9001 IgG left, IgM right; 9004 Inositol likewise). The export lists
both halves under the same plate id.

Because a class is named only once ([§1](#1-what-kind-of-file-it-is)), the second
assay's settings object is invisible in the raw text; the reader would take the
first assay's layout and pin it to the whole plate, and the second half would be
measured against the wrong curve — 28 % off on average on the file examined. This
package therefore recognises a split plate by the number of `.asy` paths in it,
reports it as a gap and computes **no** quantitative values for such a plate. The
densities, labels and sample ids are still read.

## 11. Classes in the file

A short run (29 kB) names these classes, in this order — the shape of a `.res`
at a glance:

```
CWell, CASTME1394Record, CASTME1394Field, CStringArray, CAssayDetails,
CAssayProtocol, CAssayHeader, CAssayLayout, CWellType, CBreakSettings,
CPipetteSettings, CDilutionGroup, CPipetteAspirate, CPipetteDispense,
CPipetteWash, CIncubateSettings, CWashStep, CWashSettings, CAspirateSettings,
CDispenseStep, CDispenseSettings, CShakeSettings, CEndpointRead,
CEndpointSettings, CMatrixField, CTableField, CBlankSettings, CQCSettings,
CQuantitativeSettings, CQualitativeSettings, CQualitativeLabel, CReportStep,
CTextExportSettings, CCSVExportSettings, CColorimeterRead, CBatchInformation,
CReading, CSetOfResults, CColorimeterDetails, CPlateEvent, CIncubationResult,
CVCUsed
```

Of these, this package reads `CWell`, `CDilutionGroup`, `CSetOfResults`,
`CAssayHeader`, `CAssayDetails`, `CQuantitativeSettings`, `CBatchInformation` and
`CPlateEvent`. `CCSVExportSettings` is worth a note: **the export configuration
lives in the plate file**, which is why two laboratories' exports of the same
assay can have different columns — and why the `.res` is the more reliable
source.

## 12. What is still unexplained

Honest gaps, not places where a guess would do:

* the i32 after the sample index in a well record (0 at an empty well, 64 at a
  well with a sample);
* the u8 before the rack position, and the three bytes after the sample id;
* the fixed block between the assay author and the plate type in `CAssayHeader`
  (the reader locates the two strings by position, not by schema);
* the report and font structures in front of the calibrator concentrations;
* the pipetting, washing and incubation step classes — they are skipped
  entirely, which is why this package cannot reconstruct a run's protocol;
* `CQualitativeSettings`: the cut-off index and the interpretation bands are
  presumably in there. A qualitative result (positive / borderline / negative) is
  therefore **not** computed by this package.
