#!/usr/bin/env python3
"""Strip identifying text from a Gemini ``.res`` file, byte for byte.

The sample files in ``tests/data`` come from a real laboratory. They are the only
proof that the reader is right, so they had to be published — but they carried an
operator's name, the company name and the laboratory's internal sample
identifiers.

**Why this is a byte-level rewrite and not a re-export.** A ``.res`` is an MFC
``CArchive`` stream: strings are stored with a length prefix and the object graph
is written back to back, with no index. Change the length of a string and every
offset after it moves; the file would no longer parse. This script therefore
replaces text **in place, with placeholders of exactly the same byte length**.
Every number — optical density, calibrator concentration, timestamp — is left
untouched, because those are what the tests check.

What is replaced:

* the operator and the users who ran the plate,
* the vendor token in reagent and plate names,
* the stem of every sample identifier, keeping a trailing dilution (``1:200``,
  ``-1280``, ``/400``) and its one-to-three letter suffix, so the files stay useful as
  examples of how laboratories encode dilutions in a sample id.

* the manufacturer's catalogue numbers (REF), which lead the assay names and the
  plate ids — together with the product name they name the manufacturer. They
  go into the local literals file like the names: ``1234_`` for the assay names,
  the digits a plate id starts with for the plates. The file names follow.

What is **not** replaced, deliberately: the rest of the plate id and the product
names in the assay names; they are the handle by which a run is discussed.
Reagent lot numbers stay as well — without them the ``CBatchInformation`` schema
could not be checked.

The companion files of a run — the instrument's own ``.csv`` export and its
``.txt`` report — get the same substitutions, so they remain a valid cross-check
against the reader's output.

Run it against your own files before sharing them::

    python tools/anonymise.py run.res out_dir/
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

#: Fixed replacements: the names of people, accounts and the vendor, one
#: ``original<TAB>placeholder`` per line. They live in a local file that is never
#: committed — listed here, they would publish exactly what is being removed.
#: Both sides must have the same byte length; the script refuses anything else
#: rather than writing a file that cannot be read.
LITERALS_FILE = Path(__file__).with_name("literals.local.tsv")


def load_literals(path: Path = LITERALS_FILE) -> dict[bytes, bytes]:
    if not path.exists():
        return {}
    table = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            before, after = line.split("\t")
            table[before.encode("latin-1")] = after.encode("latin-1")
    return table


LITERALS = load_literals()

#: A sample identifier: a stem, then optionally a dilution and a short suffix.
#: Only the stem is replaced. A number after a blank (``QC 12345``) is part of the
#: identifier, not a dilution — keeping it published the lab's own numbering.
SAMPLE = re.compile(
    r"^(?P<stem>.*?)(?P<tail>(?:\s*(?:1\s*[:/]\s*|[-_/])\d{1,5}\s*[A-Za-z]{0,3})?)$"
)

#: Files of the same run that carry the same texts in plain form.
COMPANIONS = (".csv", ".txt")


_BASE36 = "0123456789abcdefghijklmnopqrstuvwxyz"


def _base36(n: int) -> str:
    out = ""
    while True:
        n, rest = divmod(n, 36)
        out = _BASE36[rest] + out
        if not n:
            return out


def _placeholder(stem: str, index: int) -> str:
    """A stem of the same length, so the file's offsets do not move.

    Base 36 for the counter, because a stem can be two characters long and two
    identifiers that end up with the same placeholder would become one sample.
    """
    if not stem:
        return stem
    n = len(stem)
    code = _base36(index)
    if len(code) + 1 <= n:
        return ("S" + code).ljust(n, "0")
    return code[-n:] if len(code) >= n else code.rjust(n, "0")


def mapping_for(sample_ids: list[str]) -> dict[bytes, bytes]:
    """Every substitution for one run: the literals plus one per sample id.

    Longest identifier first, so that ``QC7 1:200`` is not half-replaced by the
    rule for ``QC7``.
    """
    table = dict(LITERALS)
    ordered = sorted({s for s in sample_ids if s}, key=len, reverse=True)
    for index, identifier in enumerate(ordered, start=1):
        match = SAMPLE.match(identifier)
        stem, tail = match.group("stem"), match.group("tail")
        table[identifier.encode("latin-1")] = (
            _placeholder(stem, index) + tail
        ).encode("latin-1")
    for before, after in table.items():
        if len(before) != len(after):
            raise ValueError("replacement %r has a different length than %r" % (after, before))
    seen: dict[bytes, bytes] = {}
    for before, after in table.items():
        if after in seen:
            raise ValueError(
                "%r and %r would both become %r — two samples would merge"
                % (seen[after], before, after)
            )
        seen[after] = before
    return table


def anonymise(raw: bytes, table: dict[bytes, bytes]) -> bytes:
    """Return the data with identifying text replaced, same length throughout.

    **One pass, longest match first, and never over its own output.** Repeated
    ``bytes.replace`` calls would work on text already written: a sample id
    ``4711-200`` becomes ``S7-200``, and the rule for the id ``200`` would then
    eat the dilution that was meant to be kept (seen as ``S13-S23``). So the data
    is scanned once from the left, and what has been written is not looked at
    again.
    """
    by_first: dict[int, list[bytes]] = {}
    for key in sorted(table, key=len, reverse=True):
        by_first.setdefault(key[0], []).append(key)
    out = bytearray()
    i = 0
    while i < len(raw):
        for key in by_first.get(raw[i], ()):
            if raw.startswith(key, i):
                out += table[key]
                i += len(key)
                break
        else:
            out.append(raw[i])
            i += 1
    if len(out) != len(raw):
        raise ValueError("length changed — the archive would not parse")
    return bytes(out)


def _renamed(name: str, table: dict[bytes, bytes]) -> str:
    """A run's files are named after its plate id, so they carry what it carried."""
    return anonymise(name.encode("latin-1"), table).decode("latin-1")


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__)
        return 2
    source, out_dir = Path(argv[1]), Path(argv[2])
    out_dir.mkdir(parents=True, exist_ok=True)

    # Read the file first, so we know which sample identifiers are in it.
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from gemini_res import read_run

    run = read_run(source)
    table = mapping_for([w.sample_id for w in run.wells])

    target = out_dir / _renamed(source.name, table)
    target.write_bytes(anonymise(source.read_bytes(), table))

    # And read it back: a file that no longer parses is worse than no sample.
    after = read_run(target)
    if [w.od for w in after.wells] != [w.od for w in run.wells]:
        raise SystemExit("the optical densities changed — refusing to write")
    print("%s -> %s (%d wells, numbers unchanged)" % (source.name, target, len(after.wells)))

    for suffix in COMPANIONS:
        companion = source.with_suffix(suffix)
        if companion.exists():
            copy = out_dir / _renamed(companion.name, table)
            copy.write_bytes(anonymise(companion.read_bytes(), table))
            print("%s -> %s" % (companion.name, copy))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
