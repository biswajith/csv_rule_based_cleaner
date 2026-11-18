"""profile + rules -> validated Plan; dry-run preview on the sample (spec §5)."""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, ValidationError

from .config import get_settings
from .connectors.base import ColumnInfo, Connector, Record
from .llm import LLM
from .ops import OpStat, apply_row, cell_text, check_plan
from .plan import Plan, PlannerOutput, RawJSON
from .profiler import Profile, profile_rows
from .prompts import PLANNER_SYSTEM, planner_user, planner_repair

MAX_EXAMPLES = 5


class PlanError(Exception):
    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


class ColumnPreview(BaseModel):
    column: str
    cells_changed: int
    cells_flagged: int
    ops: list[OpStat]
    examples: list[tuple[str, str]]
    flag_examples: list[tuple[str, str]]
    llm_cells: int
    llm_unique: int


class Preview(BaseModel):
    sampled_rows: int
    total_rows: int | None
    columns: list[ColumnPreview]
    llm_cells_sample: int
    llm_unique_sample: int
    llm_cells_estimate: int | None
    warnings: list[str]


class PlanResult(BaseModel):
    plan: Plan
    preview: Preview
    usage: dict[str, int]


def _select(connector: Connector, columns: list[str] | None) -> list[ColumnInfo]:
    infos = connector.column_info()
    if columns is None:
        return infos
    names = {c.name for c in infos}
    unknown = [c for c in columns if c not in names]
    if unknown:
        raise ValueError(f"unknown column(s): {', '.join(unknown)} "
                         f"(available columns: {', '.join(sorted(names))})")
    return [c for c in infos if c.name in set(columns)]


def profile(connector: Connector, columns: list[str] | None = None) -> Profile:
    rows = connector.sample(get_settings().sample_size)
    return profile_rows(rows, _select(connector, columns), connector.description, connector.count())


def _profile_json(prof: Profile, limit: int) -> str:
    def dump(p: Profile) -> str:
        # `source` (file name / connection description) is not needed to plan: keep it out
        return json.dumps(p.model_dump(exclude={"source"}), separators=(",", ":"),
                          ensure_ascii=False)

    text = dump(prof)
    if len(text) <= limit:
        return text
    slim = prof.model_copy(deep=True)
    for c in slim.columns:
        c.top_values = c.top_values[:5]
        c.rare_examples = c.rare_examples[:5]
    text = dump(slim)
    if len(text) <= limit:
        return text
    for c in slim.columns:
        c.all_values = None
    text = dump(slim)
    if len(text) <= limit:
        return text
    raise ValueError("Too many columns to plan at once; pass --columns")


def _format_schema_errors(e: ValidationError) -> list[str]:
    """pydantic errors as `columns[0].ops[2].case.mode: <message>` lines."""
    out = []
    for err in e.errors():
        path = ""
        for p in err["loc"]:
            path += f"[{p}]" if isinstance(p, int) else (f".{p}" if path else str(p))
        out.append(f"{path}: {err['msg']}" if path else str(err["msg"]))
    return out


def plan(connector: Connector, rules: list[str], llm: LLM,
         columns: list[str] | None = None) -> PlanResult:
    rules = [r.strip() for r in rules if r and r.strip()]
    if not rules:
        raise ValueError("at least one non-empty rule is required")
    settings = get_settings()
    infos = connector.column_info()
    rows = connector.sample(settings.sample_size)
    prof = profile_rows(rows, _select(connector, columns), connector.description, connector.count())
    profile_json = _profile_json(prof, settings.planner_max_profile_chars)

    messages: list[tuple[str, str]] = [("system", PLANNER_SYSTEM),
                                       ("human", planner_user(rules, profile_json))]
    errors: list[str] = []
    for attempt in range(settings.planner_max_repairs + 1):
        data, raw = llm.complete_json(messages, RawJSON)
        try:
            out = PlannerOutput.model_validate(data.model_dump())
        except ValidationError as e:
            errors = _format_schema_errors(e)
        else:
            candidate = Plan(rules=rules, columns=out.columns, notes=out.notes)
            errors, _ = check_plan(candidate, infos)
        if not errors:
            break
        messages += [("ai", raw), ("human", planner_repair(errors))]
    else:
        raise PlanError(errors)

    preview = dry_run(candidate, rows, connector.count(), infos, connector.null_equals_empty)
    return PlanResult(plan=candidate, preview=preview, usage=llm.usage.as_dict())


# Ops that can legitimately do nothing on a sample (trim, case, ... or validate) never warn.
NO_EFFECT_OPS = frozenset({"regex_replace", "reformat", "translate", "map_values",
                           "map_from_column", "null_if", "parse_date"})


def _no_effect(op, changed: int, flagged: int, llm_cells: int) -> bool:
    """The "no effect on sample" rule (spec §5.3); the web UI computes the same rule."""
    if op.op == "llm_fix":
        return llm_cells == 0
    return op.op in NO_EFFECT_OPS and changed == 0 and flagged == 0


def dry_run(plan_: Plan, rows: list[Record], total_rows: int | None, columns: list[ColumnInfo],
            null_equals_empty: bool) -> Preview:
    infos = {c.name: c for c in columns}
    _, warnings = check_plan(plan_, columns)
    n_changed: dict[str, int] = {}
    n_flagged: dict[str, int] = {}
    stats: dict[tuple[str, int], list[int]] = {}
    examples: dict[str, dict[tuple[str, str], None]] = {}
    flag_examples: dict[str, dict[tuple[str, str], None]] = {}
    llm_cells: dict[str, int] = {}
    llm_keys: dict[str, set] = {}

    for row in rows:
        res = apply_row(plan_, row, None, columns=infos, null_equals_empty=null_equals_empty)
        for col, (before, after, _src) in res.changed.items():
            n_changed[col] = n_changed.get(col, 0) + 1
            ex = examples.setdefault(col, {})
            if len(ex) < MAX_EXAMPLES:
                ex.setdefault((cell_text(before), cell_text(after)), None)
        flagged_cols = set()
        for f in res.flags:
            flagged_cols.add(f.column)
            fe = flag_examples.setdefault(f.column, {})
            if len(fe) < MAX_EXAMPLES:
                fe.setdefault((cell_text(f.value), f.message), None)
        for col in flagged_cols:
            n_flagged[col] = n_flagged.get(col, 0) + 1
        for k, (c, f) in res.op_stats.items():
            s = stats.setdefault(k, [0, 0])
            s[0] += c
            s[1] += f
        for key in res.llm_keys:
            llm_cells[key.column] = llm_cells.get(key.column, 0) + 1
            llm_keys.setdefault(key.column, set()).add(key)

    cols: list[ColumnPreview] = []
    for cp in plan_.columns:
        ops: list[OpStat] = []
        for i, op in enumerate(cp.ops):
            if not op.enabled:
                continue
            ch, fl = stats.get((cp.column, i), [0, 0])
            ops.append(OpStat(op_index=i, op=op.op, changed=ch, flagged=fl))
            if rows and _no_effect(op, ch, fl, llm_cells.get(cp.column, 0)):
                warnings.append(f"column '{cp.column}' op {i} ({op.op}): no effect on sample")
        cols.append(ColumnPreview(
            column=cp.column,
            cells_changed=n_changed.get(cp.column, 0),
            cells_flagged=n_flagged.get(cp.column, 0),
            ops=ops,
            examples=list(examples.get(cp.column, {})),
            flag_examples=list(flag_examples.get(cp.column, {})),
            llm_cells=llm_cells.get(cp.column, 0),
            llm_unique=len(llm_keys.get(cp.column, ())),
        ))
    cells = sum(c.llm_cells for c in cols)
    unique = sum(c.llm_unique for c in cols)
    estimate = round(cells * total_rows / len(rows)) if rows and total_rows is not None else None
    return Preview(sampled_rows=len(rows), total_rows=total_rows, columns=cols,
                   llm_cells_sample=cells, llm_unique_sample=unique,
                   llm_cells_estimate=estimate, warnings=warnings)


def preview(connector: Connector, plan_: Plan) -> Preview:
    infos = connector.column_info()
    errors, _ = check_plan(plan_, infos)
    if errors:
        raise PlanError(errors)
    rows = connector.sample(get_settings().sample_size)
    return dry_run(plan_, rows, connector.count(), infos, connector.null_equals_empty)
