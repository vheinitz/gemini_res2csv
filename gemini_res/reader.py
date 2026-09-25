"""Reader for the Stratec Gemini result file ``*.res`` — structural, not heuristic.

**What the file is.** An MFC ``CArchive`` serialisation; Gemini.exe is an MFC
application. The library rules this reader relies on:

* Strings are ``CString``: one length byte, ``0xFF`` + WORD from 255 on,
  ``0xFFFF`` + DWORD from 65535 on, then the bytes (cp1252).
* An object starts with a WORD tag: ``0xFFFF`` = new class, followed by WORD
  schema, WORD name length, class name; ``0x8000 | n`` = an object of a class
  already named (``n`` counts classes **and** objects, which is why the numbers
  are large); ``n < 0x8000`` = a back reference to an object already stored.
* A ``CObArray`` writes its count as a WORD (``0x60 0x00`` = 96 wells).

**What the reader reads** — verified against file pairs that carry Gemini's own
CSV export: the header (plate id, user, timestamp, temperature), the 96 ``CWell``
(sample index, rack position, sample id; column-major A1…H1, A2…), the **layout**
from the ``CDilutionGroup`` well maps (CAL_A…, PC, NC, CO, Sample), the **optical
density matrix** from ``CSetOfResults`` (8 × 12 doubles), calibrator
concentrations and unit from ``CQuantitativeSettings``, reagents with lot numbers
from ``CBatchInformation``, plate events with timestamps from ``CPlateEvent``, the
assay name from ``CAssayHeader``.

**The quantitative results (U/ml) are not in the file** — "the calculation is
performed again before it is displayed" (IFU p. 4-67). They are recomputed; see
``gemini_res.curve``.

**What it does not read:** anything whose schema is not verified (pipetting,
washing, incubation steps). Those classes are skipped, not guessed.

**Why anchored rather than sequential.** A ``CArchive`` can only be read from end
to end if every schema of every class is known — forty classes, most of them of
no use here. This reader reads the header and the wells **sequentially** (there
every byte is accounted for) and otherwise jumps to the class names the archive
itself spells out. Every schema is checked against several files and fails loudly
when a byte does not fit (``ResFormatError``) — no number is better than a wrong
one.
"""

from __future__ import annotations

import datetime as _dt
import re
import struct
from dataclasses import dataclass, field
from pathlib import Path

ROWS = "ABCDEFGH"
COLUMNS = 12
ENCODING = "cp1252"

#: OLE automation date: days since 1899-12-30, as a double.
_OLE_EPOCH = _dt.datetime(1899, 12, 30)


class ResFormatError(ValueError):
    """The file does not match the verified schema at some point."""


def well_name(index: int) -> str:
    """Column-major numbering: 0 = A1, 1 = B1, …, 8 = A2, …, 95 = H12."""
    return "%s%d" % (ROWS[index % 8], index // 8 + 1)


def ole_date(value: float) -> _dt.datetime:
    return _OLE_EPOCH + _dt.timedelta(days=value)


# --------------------------------------------------------------------------- #
# The cursor over the archive
# --------------------------------------------------------------------------- #


class Archive:
    """A read cursor with the primitive types of ``CArchive``."""

    def __init__(self, data: bytes, pos: int = 0):
        self.d = data
        self.p = pos

    def bytes(self, n: int) -> bytes:
        if self.p + n > len(self.d):
            raise ResFormatError("end of file at offset 0x%x" % self.p)
        s = self.d[self.p : self.p + n]
        self.p += n
        return s

    def u8(self) -> int:
        return self.bytes(1)[0]

    def i16(self) -> int:
        return struct.unpack("<h", self.bytes(2))[0]

    def u16(self) -> int:
        return struct.unpack("<H", self.bytes(2))[0]

    def i32(self) -> int:
        return struct.unpack("<i", self.bytes(4))[0]

    def dbl(self) -> float:
        return struct.unpack("<d", self.bytes(8))[0]

    def cstr(self) -> str:
        n = self.u8()
        if n == 0xFF:
            n = self.u16()
            if n == 0xFFFF:
                n = struct.unpack("<I", self.bytes(4))[0]
        return self.bytes(n).decode(ENCODING, errors="replace")

    def expect(self, value: bytes, what: str) -> None:
        got = self.bytes(len(value))
        if got != value:
            raise ResFormatError(
                "%s: expected %s, found %s at 0x%x"
                % (what, value.hex(), got.hex(), self.p - len(value))
            )

    def class_tag_ahead(self) -> bool:
        """Is the next WORD a ``0x8000|n`` tag (object of a known class)?"""
        if self.p + 2 > len(self.d):
            return False
        tag = struct.unpack_from("<H", self.d, self.p)[0]
        return tag != 0xFFFF and (tag & 0x8000) != 0 and (tag & 0x7FFF) < 0x4000

    def class_tag(self) -> int:
        return self.u16()

    def class_name(self) -> str:
        """Read ``0xFFFF, schema, length, name`` — the head of a new class."""
        self.expect(b"\xff\xff", "new class")
        self.u16()  # schema
        n = self.u16()
        return self.bytes(n).decode("ascii")


def _classes(data: bytes) -> dict[str, int]:
    """Where each class is first named — name → offset of its tag."""
    found = {}
    for m in re.finditer(rb"\xff\xff(..)(..)([A-Z][A-Za-z0-9]{2,40})", data):
        n = struct.unpack("<H", m.group(2))[0]
        name = m.group(3)[:n].decode("ascii", errors="ignore")
        if len(name) == n and name not in found:
            found[name] = m.start()
    return found


# --------------------------------------------------------------------------- #
# The things
# --------------------------------------------------------------------------- #


@dataclass
class Well:
    index: int
    name: str
    sample_index: int  # -1 = no sample record (calibrator, control, empty)
    rack_position: str
    sample_id: str
    used: bool = True
    flagged: bool = False  # Gemini flagged the result (CResultFlag)
    group: str = ""  # name of the dilution group (CAL_A_…, PC_…, Sample)
    label: str = ""  # CalA … CalF, PC1, NC1, CO1, T1 …
    od: float | None = None

    @property
    def is_sample(self) -> bool:
        return self.sample_index >= 0


@dataclass
class Reagent:
    """A line of ``CBatchInformation``: a reagent and the lot it came from.

    The **first** line of that list is not a reagent but the assay itself (see
    :attr:`Run.assay_name`). An empty ``lot`` means the laboratory did not record
    one; the instrument writes ``.`` or ``..`` in that case.
    """

    name: str
    lot: str


@dataclass
class Event:
    timestamp: _dt.datetime
    text: str
    number: int


@dataclass
class Run:
    file: str
    plate_id: str
    user: str
    timestamp: _dt.datetime
    #: The temperature recorded with the plate — the instrument's, 21…25 °C in
    #: every file seen, **not** the incubation temperature of the assay (a
    #: "27C" assay ran at 22.43 °C ambient).
    temperature: float | None
    assay: str = ""
    assay_file: str = ""
    #: Every assay that ran on this plate — usually one, two on a split plate
    #: (see :func:`_assay_files`).
    assays: list[str] = field(default_factory=list)
    operator: str = ""
    plate: str = ""
    description: str = ""
    unit: str = ""
    calibrator_concentrations: list[float] = field(default_factory=list)
    wells: list[Well] = field(default_factory=list)
    groups: dict[str, list[str]] = field(default_factory=dict)
    reagents: list[Reagent] = field(default_factory=list)
    events: list[Event] = field(default_factory=list)
    od: list[list[float]] = field(default_factory=list)  # [row][column]
    #: What the reader could not read with certainty — instead of guessing.
    gaps: list[str] = field(default_factory=list)

    @property
    def assay_name(self) -> str:
        """The assay name the instrument's own export writes.

        **Two names, and they can differ.** ``CAssayHeader`` carries the name of
        the assay *definition*; the plate names the ``.asy`` *file* it ran. Save a
        copy of an assay under a new file name and the definition keeps its old
        name — in ``320424220465M27LBR`` the header says
        ``3204_Cardiolipin-GM_IgM_V2_QC`` while the file, and Gemini's export, say
        ``3204_Cardiolipin-GM_IgM_27C``. The file name is what a laboratory reads
        off its result sheet, so that is what goes into the CSV.
        """
        if self.assays:
            return self.assays[0].rsplit(".", 1)[0]
        # The first entry of CBatchInformation carries the same name — in every
        # file examined it is the assay file, not the definition. It serves as a
        # fallback for a file whose .asy path is missing.
        if self.reagents:
            return self.reagents[0].name
        return self.assay

    def well(self, name: str) -> Well:
        for w in self.wells:
            if w.name == name:
                return w
        raise KeyError(name)

    @property
    def start(self) -> _dt.datetime | None:
        return self.events[0].timestamp if self.events else None

    @property
    def end(self) -> _dt.datetime | None:
        return self.events[-1].timestamp if self.events else None

    def samples(self) -> list[dict]:
        """One row per sample index: sample id, wells, single ODs and the mean.

        This is where a duplicate determination is bundled into one result.

        **A flagged well does not count towards the mean.** In run
        320424220465MRTLBR well B4 carries the flag; its own OD is 0.027, A4 reads
        0.079, and the instrument's export gives the pair a mean of 0.079 — A4
        alone. The flag is in the well record (``CResultFlag``), so the rule is in
        the file, not in an interpretation of the numbers. The value of the
        flagged well is kept in ``od_excluded``, because dropping it silently
        would hide why the mean equals a single measurement.
        """
        per_index: dict[int, dict] = {}
        for w in self.wells:
            if not w.is_sample:
                continue
            entry = per_index.setdefault(
                w.sample_index,
                {"sample_index": w.sample_index, "label": w.label, "sample_id": w.sample_id,
                 "rack_position": w.rack_position, "wells": [], "od": [], "od_excluded": []},
            )
            entry["wells"].append(w.name)
            if w.od is not None:
                entry["od_excluded" if w.flagged else "od"].append(w.od)
        rows = []
        for entry in sorted(per_index.values(), key=lambda e: e["sample_index"]):
            values = entry["od"]
            entry["od_mean"] = sum(values) / len(values) if values else None
            rows.append(entry)
        return rows

    def controls(self) -> list[dict]:
        """Calibrators and controls with their wells and ODs."""
        rows = []
        groups = dict(self.groups)
        # Control wells without a layout: each on its own — a mean over all of
        # them together would be a number without meaning (32 wells, 32 things).
        for w in self.wells:
            if w.label == "C?":
                groups["Control %s" % w.name] = [w.name]
        for group, wells in groups.items():
            if group == "Sample":
                continue
            # Same rule as for the samples: a flagged well is reported, not counted.
            ods = [self.well(n).od for n in wells
                   if self.well(n).od is not None and not self.well(n).flagged]
            excluded = [self.well(n).od for n in wells
                        if self.well(n).od is not None and self.well(n).flagged]
            rows.append(
                {
                    "group": group,
                    "role": _role(group),
                    "label": self.well(wells[0]).label if wells else "",
                    "wells": wells,
                    "od": ods,
                    "od_excluded": excluded,
                    "od_mean": sum(ods) / len(ods) if ods else None,
                }
            )
        return rows

    def as_dict(self) -> dict:
        return {
            "file": self.file,
            "plate_id": self.plate_id,
            "assay": self.assay,
            "assay_name": self.assay_name,
            "assay_file": self.assay_file,
            "assays": self.assays,
            "user": self.user,
            "operator": self.operator,
            "plate": self.plate,
            "description": self.description,
            "timestamp": self.timestamp.isoformat(timespec="seconds"),
            "start": self.start.isoformat(timespec="seconds") if self.start else None,
            "end": self.end.isoformat(timespec="seconds") if self.end else None,
            "temperature": self.temperature,
            "unit": self.unit,
            "calibrator_concentrations": self.calibrator_concentrations,
            "reagents": [{"name": r.name, "lot": r.lot} for r in self.reagents],
            "groups": self.groups,
            "wells": [
                {"well": w.name, "label": w.label, "group": w.group, "sample_id": w.sample_id,
                 "rack_position": w.rack_position, "od": _r(w.od), "flagged": w.flagged}
                for w in self.wells
            ],
            "samples": [_rounded(row) for row in self.samples()],
            "controls": [_rounded(row) for row in self.controls()],
            "events": [
                {"timestamp": e.timestamp.isoformat(timespec="seconds"), "text": e.text}
                for e in self.events
            ],
            "gaps": self.gaps,
        }


def _r(value):
    return None if value is None else round(value, 4)


def _rounded(row: dict) -> dict:
    row = dict(row)
    row["od"] = [_r(v) for v in row["od"]]
    row["od_excluded"] = [_r(v) for v in row["od_excluded"]]
    row["od_mean"] = _r(row["od_mean"])
    return row


def _role(group: str) -> str:
    g = group.upper()
    if g.startswith("CAL"):
        return "CALIBRATOR"
    if g.startswith("PC"):
        return "POSITIVE CONTROL"
    if g.startswith("NC"):
        return "NEGATIVE CONTROL"
    if g.startswith("CO"):
        return "CUTOFF"
    if g == "SAMPLE":
        return "SAMPLE"
    return "CONTROL"


def _label(group: str, ordinal: int) -> str:
    """"CalA" from ``CAL_A_Cardiolipin-GM``, "PC1" from ``PC_…`` — the name
    Gemini writes as *Layout Label* in its own export."""
    m = re.match(r"CAL_([A-Z])_", group, re.I)
    if m:
        return "Cal" + m.group(1).upper()
    for prefix in ("PC", "NC", "CO"):
        if group.upper().startswith(prefix):
            return "%s%d" % (prefix, ordinal)
    return group


# --------------------------------------------------------------------------- #
# Reading
# --------------------------------------------------------------------------- #


def _header(a: Archive, fields: dict) -> None:
    """Header: u32 (17), u8, OLE date, plate id, user, 3 × u32, temperature.

    The three u32 in the middle are a flag and the plate geometry; 8 × 12 is
    checked, because everything downstream assumes a 96-well plate.
    """
    a.i32()  # version, 17 in every file seen
    a.u8()
    fields["timestamp"] = ole_date(a.dbl())
    fields["plate_id"] = a.cstr()
    fields["user"] = a.cstr()
    a.i32()
    rows = a.i32()
    columns = a.i32()
    if (rows, columns) != (8, COLUMNS):
        raise ResFormatError("plate %d×%d instead of 8×12" % (rows, columns))
    fields["temperature"] = round(a.dbl(), 2)


def _wells(data: bytes, classes: dict[str, int]) -> list[Well]:
    """The 96 ``CWell``, read sequentially.

    Schema (verified on empty, occupied, unused and flagged wells): u8 11, u8
    flag (1 = result flagged, ``CResultFlag``), i32 occupancy (-1 = unused; 0/1 =
    plate half), i32 sample index (-1 = none), i32, u8, CString rack position,
    CString sample id, 3 bytes, u8 1, i32 -1, i32 1, u8, CString sample id
    (again). The WORD count 96 precedes the array.
    """
    start = classes.get("CWell")
    if start is None:
        raise ResFormatError("no class CWell")
    a = Archive(data, start - 2)
    count = a.u16()
    if count != 96:
        raise ResFormatError("%d wells instead of 96" % count)
    a.class_name()
    wells = []
    for index in range(96):
        if index:
            a.class_tag()
        kind = a.u8()
        if kind != 11:
            raise ResFormatError("well %d: kind %d instead of 11" % (index, kind))
        flagged = a.u8() != 0
        used = a.i32() != -1
        sample_index = a.i32()
        a.i32()
        a.u8()
        rack = a.cstr()
        sample_id = a.cstr()
        a.bytes(3)
        a.u8()
        a.i32()
        a.i32()
        a.u8()
        sample_id2 = a.cstr()
        wells.append(
            Well(
                index=index, name=well_name(index), sample_index=sample_index,
                rack_position=rack, sample_id=sample_id or sample_id2, used=used,
                flagged=flagged,
            )
        )
    return wells


_GROUP_MAP = re.compile(rb"\x08\x00\x00\x00\x0c\x00\x00\x00..\x60\x00")


def _groups(data: bytes) -> dict[str, list[str]]:
    """The dilution groups with their well map (``CDilutionGroup``).

    Behind its name each group carries 8, 12, a WORD, the count 96 and then **96
    bytes**: 1 where the well belongs to the group. That is the plate layout —
    CAL_A in A1/B1, Sample from A3 on — and it is in the file, not in an
    assumption about the grid.
    """
    groups: dict[str, list[str]] = {}
    for m in _GROUP_MAP.finditer(data):
        end = m.start() - 4  # before it: i32 0
        name = None
        for length in range(1, 80):
            p = end - length - 1
            if p >= 0 and data[p] == length:
                candidate = data[p + 1 : end]
                if candidate and all(32 <= b < 127 for b in candidate):
                    name = candidate.decode("ascii")
                    break
        if name is None:
            continue
        chart = data[m.end() : m.end() + 96]
        wells = [well_name(i) for i in range(96) if chart[i]]
        if wells:
            groups[name] = wells
    return groups


def _od_matrix(data: bytes, classes: dict[str, int]) -> list[list[float]]:
    """``CSetOfResults``: u8, i32 rows, i32 columns, rows × columns doubles."""
    start = classes.get("CSetOfResults")
    if start is None:
        raise ResFormatError("no class CSetOfResults")
    a = Archive(data, start)
    a.class_name()
    a.u8()
    rows, columns = a.i32(), a.i32()
    if (rows, columns) != (8, COLUMNS):
        raise ResFormatError("OD matrix %d×%d instead of 8×12" % (rows, columns))
    return [[a.dbl() for _ in range(columns)] for _ in range(rows)]


def _assay_header(data: bytes, classes: dict[str, int], run: Run) -> None:
    """``CAssayHeader``: 8 bytes of flags, OLE date, i32, OLE date, CString
    assay, CString author of the assay, … then plate and description.

    What comes first is read by schema; the plate type and the description follow
    a block whose schema is not established — they are located by position.
    """
    start = classes.get("CAssayHeader")
    if start is None:
        run.gaps.append("CAssayHeader missing")
        return
    a = Archive(data, start)
    a.class_name()
    a.bytes(8)  # u8 1, u16, u8 13, i32 0 — flags of the header
    a.dbl()  # OLE date: created
    a.i32()
    a.dbl()  # OLE date: last changed
    run.assay = a.cstr()
    run.operator = a.cstr()
    # Then a block of fixed length (identifiers); the plate type is the next
    # readable CString, the description the one after it.
    rest = data[a.p : a.p + 400]
    texts = _readable_strings(rest, at_least=4)
    if texts:
        run.plate = texts[0]
    if len(texts) > 1:
        run.description = " ".join(texts[1:3])
    # The assay file is spelled out in CAssayDetails as a full path.
    details = classes.get("CAssayDetails")
    if details is not None:
        for text in _readable_strings(data[details : details + 400], at_least=6):
            if ".asy" in text.lower():
                run.assay_file = text
                break


def _assay_files(data: bytes) -> list[str]:
    """The names of all ``.asy`` files of the plate — one per assay.

    **Why this is needed:** a plate can carry **two** assays, each on its half of
    the columns and with its own calibrators (in one laboratory's archive, 21 of
    98 runs: 3204 IgG left, IgM right; 3208 Inositol likewise). The export lists
    both under the same plate id, and the ``.res`` contains both.

    The class name does not show it: ``CArchive`` writes a name only for the
    **first** object and refers to the class by number afterwards — so
    ``CQuantitativeSettings`` occurs exactly once in the raw text although the
    file holds two such objects. The paths of the assay files, by contrast, are
    spelled out, once per assay.
    """
    found = []
    for hit in re.finditer(rb"[\x20-\x7e]{3,80}\.asy", data, re.I):
        name = hit.group().decode(ENCODING, errors="replace").split("\\")[-1]
        if name not in found:
            found.append(name)
    return found


def _readable_strings(rest: bytes, *, at_least: int) -> list[str]:
    """Every length-prefixed, printable ``CString`` in a stretch of bytes."""
    found = []
    i = 0
    while i < len(rest) - 1:
        n = rest[i]
        if at_least <= n <= 120 and i + 1 + n <= len(rest):
            s = rest[i + 1 : i + 1 + n]
            if all(32 <= b < 127 or b >= 0xC0 for b in s):
                found.append(s.decode(ENCODING, errors="replace"))
                i += 1 + n
                continue
        i += 1
    return found


def _quantitative(data: bytes, classes: dict[str, int], run: Run) -> None:
    """``CQuantitativeSettings``: the unit and the calibrator concentrations.

    The concentrations follow the unit ("MPL/ml", "U/ml") as a u8 count plus n
    doubles; they are located by that shape, because the schema in front of them
    (report columns with fonts) is not established.
    """
    start = classes.get("CQuantitativeSettings")
    end = classes.get("CQualitativeSettings", start + 2000 if start else 0)
    if start is None:
        run.gaps.append("CQuantitativeSettings missing")
        return
    region = data[start:end]
    for m in re.finditer(rb"\x01\x01\x01\x00\x00\x00\x00\x01([\x02-\x10])\x00\x00\x00", region):
        n = m.group(1)[0]
        p = m.end()
        if p + 8 * n > len(region):
            continue
        values = list(struct.unpack_from("<%dd" % n, region, p))
        if all(v >= 0 for v in values) and values == sorted(values):
            run.calibrator_concentrations = [round(v, 4) for v in values]
            # The unit is the CString immediately in front of this block.
            before = region[max(0, m.start() - 40) : m.start()]
            texts = _readable_strings(before, at_least=2)
            if texts:
                run.unit = texts[-1]
            return
    run.gaps.append("calibrator concentrations not found")


def _batch(data: bytes, classes: dict[str, int], run: Run) -> None:
    """``CBatchInformation``: per reagent u8 8, CString lot, CString name, 16
    bytes; the first without a tag, the rest with the tag of the class."""
    start = classes.get("CBatchInformation")
    if start is None:
        run.gaps.append("CBatchInformation missing")
        return
    a = Archive(data, start)
    a.class_name()
    first = True
    while True:
        if not first:
            if not a.class_tag_ahead():
                break
            a.class_tag()
        first = False
        if a.u8() != 8:
            break
        lot = a.cstr()
        name = a.cstr()
        a.bytes(16)
        if not name:
            break
        # "." and ".." are what the instrument writes when no lot was recorded;
        # neither is a lot number, and an empty field says so more plainly.
        run.reagents.append(Reagent(name=name, lot="" if lot.strip(".") == "" else lot))


def _events(data: bytes, classes: dict[str, int], run: Run) -> None:
    """``CPlateEvent``: i32 1, u8, OLE date, CString, i32 number, u8 — repeated."""
    start = classes.get("CPlateEvent")
    if start is None:
        run.gaps.append("CPlateEvent missing")
        return
    a = Archive(data, start)
    a.class_name()
    first = True
    while True:
        if not first:
            if not a.class_tag_ahead():
                break
            a.class_tag()
        first = False
        try:
            a.i32()
            a.u8()
            when = a.dbl()
            text = a.cstr()
            number = a.i32()
            a.u8()
        except ResFormatError:
            break
        # A plausible OLE date (1982…2119) is the end marker: past the last
        # event the next bytes are another class, and they read as nonsense.
        if not (30000 < when < 80000):
            break
        run.events.append(Event(timestamp=ole_date(when), text=text, number=number))


def read_run(path: str | Path) -> Run:
    """Read one ``.res`` — the run with wells, layout, ODs and controls."""
    path = Path(path)
    data = path.read_bytes()
    classes = _classes(data)
    fields: dict = {}
    _header(Archive(data), fields)
    run = Run(file=path.name, **fields)
    run.wells = _wells(data, classes)
    run.groups = _groups(data)
    run.od = _od_matrix(data, classes)
    _assay_header(data, classes, run)
    run.assays = _assay_files(data)
    _quantitative(data, classes, run)
    _batch(data, classes, run)
    _events(data, classes, run)

    # Pin the layout to the wells: group, label, OD.
    group_of_well = {}
    counter: dict[str, int] = {}
    for group, wells in run.groups.items():
        prefix = group.split("_")[0].upper()
        counter[prefix] = counter.get(prefix, 0) + 1
        for name in wells:
            group_of_well[name] = (group, _label(group, counter[prefix]))
    for w in run.wells:
        if not w.used:
            # Unused wells carry 0.0 in the matrix and often sit inside the
            # assay's sample map — neither says anything about a value.
            continue
        # The reader's raw value, unrounded: Gemini computes its means from it
        # and rounds only when writing. Rounding first is off by a thousandth
        # at 1.2215.
        w.od = run.od[ROWS.index(w.name[0])][int(w.name[1:]) - 1]
        group, label = group_of_well.get(w.name, ("", ""))
        w.group = group
        if w.is_sample:
            w.label = "T%d" % (w.sample_index + 1)
        elif label:
            w.label = label
        else:
            # **No pipetting protocol, no layout** (runs pipetted by hand, "man"
            # in the plate name): the file does not name the control wells. They
            # are used and carry no sample — that is all the file says, and all
            # the reader says.
            w.group = "Control"
            w.label = "C?"
    if not run.groups and any(w.label == "C?" for w in run.wells):
        run.gaps.append(
            "control layout not in the file (no pipetting protocol): "
            "%d used wells without a sample, reported as \"C?\""
            % sum(1 for w in run.wells if w.label == "C?")
        )
    return run


def calibrator_points(run: Run) -> list[tuple[float, float]]:
    """(concentration, OD) per **single** calibrator well — input of the curve.

    The concentrations are listed in order in ``calibrator_concentrations`` and
    belong to the labels CalA, CalB, … in that same order.
    """
    names = ["Cal" + letter for letter in "ABCDEFGH"]
    mapping = dict(zip(names, run.calibrator_concentrations))
    # A flagged standard is left out, the same rule the means follow. No file in
    # the sample set carries a flagged calibrator, so this half of the rule is
    # consistent rather than verified against an export.
    return [
        (mapping[w.label], w.od)
        for w in run.wells
        if w.used and w.od is not None and not w.flagged and w.label in mapping
    ]


def split_plate(run: Run) -> bool:
    """Does the plate carry **more than one** assay?

    Two assays side by side on one plate are common: 3204 IgG left, IgM right,
    each with its own calibrators in its own half.

    **Why this is checked and not merely noted:** so far the reader takes the
    layout of the **first** assay and pins it to the whole plate; the wells of
    the second are called "Sample" in it. Computing quantitative results anyway
    would give the second half the curve of the first. Measured on one IgG/IgM
    plate: the first half agrees to 0.02 %, the second is off by 28 % on average.
    Numbers like that must not be produced.
    """
    return len(run.assays) > 1


def calibration_curve(run: Run):
    """The calibration curve of the run — or ``None`` if it cannot be fitted.

    What is computed, and how closely it matches Gemini, is documented in
    ``gemini_res.curve``. What cannot be done is recorded as a gap, not guessed.
    """
    from . import curve as _curve

    if split_plate(run):
        run.gaps.append(
            "split plate: %s — the reader only knows the layout of the first "
            "assay, so no quantitative results" % ", ".join(run.assays)
        )
        return None
    try:
        fitted = _curve.curve_from(calibrator_points(run))
    except _curve.NoCurve as error:
        run.gaps.append("no calibration curve: %s" % error)
        return None
    if not fitted.well_determined:
        # A statement about the **run**, not about the computation: Gemini
        # arrives at the same numbers. Whoever judges the standard series should
        # know that the curve is not uniquely determined there.
        if fitted.top > 0:
            run.gaps.append(
                "calibration curve not saturated: highest standard at %.0f %% of "
                "the upper asymptote"
                % (max(od for _, od in fitted.points) / fitted.top * 100)
            )
        else:
            # ``top`` can come out zero or negative when the fit derails; then
            # there is no share to quote, and the message should still be made.
            run.gaps.append("calibration curve unusable: upper asymptote %.3f" % fitted.top)
    return fitted
