"""Column profiling from a sample. No LLM. The profile is what the planner sees (spec §5.1)."""

from __future__ import annotations

import re
from collections import Counter
from datetime import date, datetime
from typing import Any

from pydantic import BaseModel

from .connectors.base import OPAQUE, ColumnInfo, Record

MAX_VALUE_CHARS = 120
TOP_VALUES = 10
TOP_SHAPES = 12
RARE_EXAMPLES = 15
ALL_VALUES_MAX_DISTINCT = 60
ALL_VALUES_MAX_CHARS = 2000

_RUN = re.compile(r"[^\W\d_]+|\s+|\d|.", re.DOTALL)
_INT = re.compile(r"[+-]?\d+")
_FLOAT = re.compile(r"[+-]?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][+-]?\d+)?")
_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2})?)?")


class ShapeStat(BaseModel):
    shape: str
    count: int
    example: str


class ColumnProfile(BaseModel):
    name: str
    cleanable: bool
    nullable: bool = True
    max_length: int | None = None
    inferred_type: str
    non_empty: int
    empty: int
    distinct_count: int
    top_values: list[tuple[str, int]]
    shapes: list[ShapeStat]
    rare_examples: list[str]
    all_values: list[str] | None = None


class Profile(BaseModel):
    source: str
    total_rows: int | None
    sampled_rows: int
    columns: list[ColumnProfile]


def shape_of(value: str) -> str:
    """digit -> 9 (count preserved), letter run -> A, whitespace run -> one space, others kept."""
    out: list[str] = []
    for m in _RUN.finditer(value[:MAX_VALUE_CHARS]):
        t = m.group()
        if t.isdigit():
            out.append("9")
        elif t[0].isalpha():
            out.append("A")
        elif t.isspace():
            out.append(" ")
        else:
            out.append(t)
    return "".join(out)


def _trunc(s: str) -> str:
    return s if len(s) <= MAX_VALUE_CHARS else s[:MAX_VALUE_CHARS] + "…"


def _kind(v: Any) -> str:
    if isinstance(v, bool):
        return "bool"
    if isinstance(v, int):
        return "int"
    if isinstance(v, float):
        return "float"
    if isinstance(v, (date, datetime)):
        return "date"
    if isinstance(v, str):
        if _INT.fullmatch(v):
            return "int"
        if _FLOAT.fullmatch(v):
            return "float"
        if v.lower() in ("true", "false"):
            return "bool"
        if _ISO_DATE.fullmatch(v):
            return "date"
        return "string"
    return "other"


def _profile_column(info: ColumnInfo, values: list[Any]) -> ColumnProfile:
    values = [None if v is OPAQUE else v for v in values]
    present = [v for v in values if v is not None and not (isinstance(v, str) and not v.strip())]
    strs = [v if isinstance(v, str) else str(v) for v in present]
    kinds = {_kind(v) for v in present}
    if kinds == {"int", "float"}:
        kinds = {"float"}
    inferred = "empty" if not kinds else (kinds.pop() if len(kinds) == 1 else "mixed")

    counts = Counter(strs)
    shape_counts: Counter[str] = Counter()
    shape_example: dict[str, str] = {}
    for s in strs:
        sh = shape_of(s)
        shape_counts[sh] += 1
        shape_example.setdefault(sh, s)
    ranked = shape_counts.most_common()
    top = ranked[:TOP_SHAPES]
    top_names = {sh for sh, _ in top}
    rare = [sh for sh, _ in reversed(ranked) if sh not in top_names][:RARE_EXAMPLES]

    all_values: list[str] | None = None
    if len(counts) <= ALL_VALUES_MAX_DISTINCT and sum(len(s) for s in counts) <= ALL_VALUES_MAX_CHARS:
        all_values = [_trunc(s) for s in sorted(counts)]

    return ColumnProfile(
        name=info.name,
        cleanable=info.cleanable,
        nullable=info.nullable,
        max_length=info.max_length,
        inferred_type=inferred,
        non_empty=len(present),
        empty=len(values) - len(present),
        distinct_count=len(counts),
        top_values=[(_trunc(s), n) for s, n in counts.most_common(TOP_VALUES)],
        shapes=[ShapeStat(shape=_trunc(sh), count=n, example=_trunc(shape_example[sh])) for sh, n in top],
        rare_examples=[_trunc(shape_example[sh]) for sh in rare],
        all_values=all_values,
    )


def profile_rows(rows: list[Record], columns: list[ColumnInfo], source: str,
                 total: int | None) -> Profile:
    return Profile(
        source=source,
        total_rows=total,
        sampled_rows=len(rows),
        columns=[_profile_column(c, [r.get(c.name) for r in rows]) for c in columns],
    )
