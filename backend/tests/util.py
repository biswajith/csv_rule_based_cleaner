from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

from scrubber.connectors.base import ColumnInfo
from scrubber.plan import Plan


def mkplan(*columns: tuple[str, list[dict[str, Any]]]) -> Plan:
    return Plan.model_validate(
        {"columns": [{"column": c, "ops": ops} for c, ops in columns]})


def infos(*names: str, **kw: Any) -> list[ColumnInfo]:
    return [ColumnInfo(name=n, cleanable=True, **kw) for n in names]


def write_csv(path: Path, rows: list[list[str]], delimiter: str = ",") -> Path:
    with path.open("w", newline="", encoding="utf-8") as f:
        csv.writer(f, delimiter=delimiter).writerows(rows)
    return path


def read_csv(path: Path) -> list[list[str]]:
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.reader(f))


def approved(plan: Plan | dict[str, Any], by: str = "Tester") -> dict[str, Any]:
    """A plan (model or dict) as a JSON-ready dict carrying a valid approval."""
    p = plan if isinstance(plan, Plan) else Plan.model_validate(plan)
    return p.with_approval(by).model_dump(mode="json")
