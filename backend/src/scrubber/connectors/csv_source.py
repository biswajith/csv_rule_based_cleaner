"""CSV connector (spec §7.1). Reads with the csv module; all cells are str."""

from __future__ import annotations

import csv
import random
from pathlib import Path
from typing import Iterator

from .base import (
    EXTRA_KEY,
    Batch,
    ColumnInfo,
    Connector,
    CSVSource,
    OutputExistsError,
    Record,
    SourceError,
    WriteBatch,
    Writer,
)


class CSVConnector(Connector):
    null_equals_empty = True

    def __init__(self, spec: CSVSource):
        self.spec = spec
        self.path = Path(spec.path)
        if not self.path.is_file():
            raise SourceError(f"CSV file not found: {self.path}")
        self._name = spec.display_name or self.path.name
        self.description = f"CSV {self._name}"
        self._header = self._read_header()
        # One validating pass: catches undecodable bytes at connect time and counts rows.
        self._count: int | None = sum(1 for _ in self._rows())

    def _read_rows(self) -> Iterator[list[str]]:
        try:
            with self.path.open(newline="", encoding=self.spec.encoding) as f:
                for row in csv.reader(f, delimiter=self.spec.delimiter):
                    yield row
        except UnicodeDecodeError as e:
            raise SourceError(
                f"cannot decode {self._name} as {self.spec.encoding}: {e.reason}; try --encoding"
            ) from e
        except LookupError as e:
            raise SourceError(f"unknown encoding {self.spec.encoding!r}") from e
        except csv.Error as e:
            raise SourceError(f"invalid CSV in {self._name}: {e}") from e

    def _read_header(self) -> list[str]:
        header = next(self._read_rows(), None)
        if not header:
            raise SourceError(f"CSV file has no header row: {self._name}")
        if any(not h.strip() for h in header):
            raise SourceError("CSV header contains an empty column name")
        if EXTRA_KEY in header:
            raise SourceError(f"CSV header contains the reserved column name {EXTRA_KEY!r}; rename it")
        dupes = sorted({h for h in header if header.count(h) > 1})
        if dupes:
            raise SourceError(f"CSV header has duplicate column names: {', '.join(dupes)}")
        return header

    def _rows(self) -> Iterator[Record]:
        n = len(self._header)
        it = self._read_rows()
        next(it, None)  # header
        for row in it:
            if not row:  # blank line: skipped, except in a one-column file where it is an empty value
                if n == 1:
                    yield {self._header[0]: ""}
                continue
            rec: Record = {c: (row[i] if i < len(row) else "") for i, c in enumerate(self._header)}
            if len(row) > n:
                rec[EXTRA_KEY] = row[n:]
            yield rec

    def column_info(self) -> list[ColumnInfo]:
        return [ColumnInfo(name=c, cleanable=True) for c in self._header]

    def count(self) -> int | None:
        return self._count

    def sample(self, n: int) -> list[Record]:
        rng = random.Random(0)
        reservoir: list[tuple[int, Record]] = []
        total = 0
        for i, rec in enumerate(self._rows()):
            total += 1
            if len(reservoir) < n:
                reservoir.append((i, rec))
            else:
                j = rng.randint(0, i)
                if j < n:
                    reservoir[j] = (i, rec)
        self._count = total
        return [rec for _, rec in sorted(reservoir, key=lambda t: t[0])]

    def iter_batches(self, size: int) -> Iterator[Batch]:
        batch: Batch = []
        for i, rec in enumerate(self._rows()):
            batch.append((i, rec))
            if len(batch) >= size:
                yield batch
                batch = []
        if batch:
            yield batch

    def check_target(self, *, in_place: bool, overwrite: bool) -> None:
        if in_place:
            raise SourceError("in-place cleaning is not supported for CSV; a new file is written")

    def writer(self, *, run_dir: Path, in_place: bool, overwrite: bool) -> Writer:
        if in_place:
            raise SourceError("in-place cleaning is not supported for CSV; a new file is written")
        return _CSVWriter(run_dir / f"{Path(self._name).stem}.cleaned.csv", self._header,
                          self.spec.delimiter, overwrite)


class _CSVWriter(Writer):
    def __init__(self, path: Path, header: list[str], delimiter: str, overwrite: bool):
        if path.exists() and not overwrite:
            raise OutputExistsError(f"output file exists: {path}")
        path.parent.mkdir(parents=True, exist_ok=True)
        self.target = str(path)
        self._header = header
        self._f = path.open("w", newline="", encoding="utf-8")
        self._w = csv.writer(self._f, delimiter=delimiter)
        self._w.writerow(header)

    def write(self, batch: WriteBatch) -> None:
        for _, rec, _ in batch:
            row = ["" if rec.get(c) is None else rec[c] for c in self._header]
            row.extend(rec.get(EXTRA_KEY) or [])
            self._w.writerow(row)

    def close(self) -> None:
        if not self._f.closed:
            self._f.close()
