"""Pure cell/row operations, plan validation, and the shared row function (spec §4, §6)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from functools import lru_cache
from typing import Any, Callable

from dateutil import parser as dateparser
from pydantic import BaseModel

from .config import get_settings
from .connectors.base import EXTRA_KEY, ColumnInfo, Record
from .plan import (
    Case, CollapseWhitespace, LLMFix, MapFromColumn, MapValues, NullIf, ParseDate, Plan,
    Reformat, RegexReplace, Translate, Trim, Validate,
)

# --------------------------------------------------------------------------- cells


def is_empty(v: Any) -> bool:
    return v is None or (isinstance(v, str) and not v.strip())


def cell_text(v: Any) -> str:
    """A cell as it appears in logs/previews: None -> "", else str(v) (not stripped)."""
    return "" if v is None else str(v)


def stringify(v: Any) -> str:
    """How another column's value is read (§4.1): None -> "", str as is, else str(); stripped."""
    return cell_text(v).strip()


@lru_cache(maxsize=2048)
def compile_regex(pattern: str, ignore_case: bool = False) -> re.Pattern[str]:
    return re.compile(pattern, re.IGNORECASE if ignore_case else 0)


def _full(pattern: str, value: str, ignore_case: bool = False) -> bool:
    return compile_regex(pattern, ignore_case).fullmatch(value) is not None


_WS = re.compile(r"\s+")
_WORD = re.compile(r"[^\W\d_]+(?:'[^\W\d_]+)*")


# --------------------------------------------------------------------------- ops


def op_trim(op: Trim, v: str) -> str:
    return v.strip()


def op_collapse_whitespace(op: CollapseWhitespace, v: str) -> str:
    return _WS.sub(" ", v)


def op_case(op: Case, v: str) -> str:
    if op.mode == "lower":
        return v.lower()
    if op.mode == "upper":
        return v.upper()
    return _WORD.sub(lambda m: m.group()[0].upper() + m.group()[1:].lower(), v)


def op_regex_replace(op: RegexReplace, v: str) -> str:
    return compile_regex(op.pattern, op.ignore_case).sub(op.replacement, v)


def op_reformat(op: Reformat, v: str) -> str:
    m = compile_regex(op.pattern, op.ignore_case).fullmatch(v)
    return m.expand(op.template) if m else v


def op_translate(op: Translate, v: str) -> str:
    out: list[str] = []
    for ch in v:
        rep = op.mapping.get(ch)
        if rep is None and op.ignore_case:
            rep = op.mapping.get(ch.upper())
            if rep is None:
                rep = op.mapping.get(ch.lower())
        out.append(ch if rep is None else rep)
    return "".join(out)


def _lookup(op: MapValues | MapFromColumn, key: str) -> str | None:
    key = key.strip()
    if key in op.mapping:
        return op.mapping[key]
    if op.ignore_case:
        return op.folded.get(key.casefold())
    return None


def op_map_values(op: MapValues, v: str) -> str:
    hit = _lookup(op, v)
    return v if hit is None else hit


def op_map_from_column(op: MapFromColumn, v: Any, source: str, column: str) -> tuple[Any, str | None]:
    """v is None or str (non-str is filtered by the caller). Returns (value, flag)."""
    if not source:
        return v, None
    mapped = _lookup(op, source)
    if mapped is None:
        return v, None
    if is_empty(v):
        return mapped, None
    same = v.strip().casefold() == mapped.strip().casefold()
    if op.mode == "overwrite":
        return (v if same else mapped), None
    if same:
        return v, None
    return v, f"{column} '{v}' conflicts with {op.source_column} '{source}' (expected '{mapped}')"


def op_null_if(op: NullIf, v: str) -> str | None:
    s = v.strip()
    if op.values and s.casefold() in {x.casefold() for x in op.values}:
        return None
    if op.pattern is not None and _full(op.pattern, s, True):
        return None
    return v


_D1, _D2 = datetime(2000, 1, 1), datetime(2001, 2, 2)


_YEAR_FIRST = re.compile(r"\d{4}[-/.]\d{1,2}[-/.]\d{1,2}")


def _read_date(op: ParseDate, s: str) -> str | None:
    """`s` (stripped, non-empty) rewritten to op.output_format, or None if unparseable.

    Idempotent: a value that already parses under output_format is kept as is, and a
    year-first value is read year-first regardless of day_first (dateutil would otherwise
    swap month and day for "2001-02-03" with dayfirst=True)."""
    try:
        datetime.strptime(s, op.output_format)
        return s
    except ValueError:
        pass
    dayfirst = op.day_first and not _YEAR_FIRST.match(s)
    try:
        a = dateparser.parse(s, dayfirst=dayfirst, default=_D1)
        b = dateparser.parse(s, dayfirst=dayfirst, default=_D2)
        if a != b:
            return None
        return a.strftime(op.output_format)
    except (ValueError, OverflowError):
        return None


def op_parse_date(op: ParseDate, v: str) -> tuple[str, str | None]:
    s = v.strip()
    if not s:
        return v, None
    out = _read_date(op, s)
    if out is None:
        return v, "Unparseable date"
    return (v if out == s else out), None


def op_validate(op: Validate, v: str | None) -> str | None:
    """Flag message or None. v is None or str."""
    if is_empty(v):
        return None if op.allow_empty else op.message
    assert v is not None
    return None if _full(op.pattern, v, op.ignore_case) else op.message


# ------------------------------------------------------------------------ llm_fix


@dataclass(frozen=True)
class LLMKey:
    column: str
    op_index: int
    value: str
    context: tuple[tuple[str, str], ...] = ()


@dataclass
class Answer:
    value: str | None
    fixed: bool
    reason: str = ""
    failure: str | None = None


Resolver = Callable[[LLMKey], "Answer | None"]


def llm_op(plan: Plan, column: str, op_index: int) -> LLMFix:
    for cp in plan.columns:
        if cp.column == column:
            op = cp.ops[op_index]
            assert isinstance(op, LLMFix)
            return op
    raise KeyError(column)


def must_match_patterns(plan: Plan, column: str, op_index: int) -> list[tuple[str, bool]]:
    """(regex, ignore_case) pairs a candidate must fully match: preceding enabled validates +
    the if_not_match pattern."""
    cp = next(c for c in plan.columns if c.column == column)
    pats = [(o.pattern, o.ignore_case) for o in cp.ops[:op_index]
            if o.enabled and isinstance(o, Validate)]
    fix = cp.ops[op_index]
    if isinstance(fix, LLMFix) and fix.when == "if_not_match" and fix.pattern:
        pats.append((fix.pattern, False))
    return pats


def target_date_format(plan: Plan, column: str, op_index: int) -> str | None:
    """output_format of the last enabled parse_date before the llm_fix (the candidate is
    re-parsed with it), else None."""
    cp = next(c for c in plan.columns if c.column == column)
    fmts = [o.output_format for o in cp.ops[:op_index] if o.enabled and isinstance(o, ParseDate)]
    return fmts[-1] if fmts else None


def _accept(candidate: str, ops: list[Any], idx: int, max_length: int | None) -> str | None:
    """Run preceding validate/parse_date (and if_not_match) on the candidate; None = rejected."""
    fix = ops[idx]
    for op in ops[:idx]:
        if not op.enabled:
            continue
        if isinstance(op, Validate):
            if not _full(op.pattern, candidate, op.ignore_case):
                return None
        elif isinstance(op, ParseDate):
            candidate, flag = op_parse_date(op, candidate)
            if flag:
                return None
    if fix.when == "if_not_match" and fix.pattern and not _full(fix.pattern, candidate):
        return None
    if max_length is not None and len(candidate) > max_length:
        return None
    return candidate


# ------------------------------------------------------------------------ row fn


@dataclass
class CellFlag:
    column: str
    op_index: int | None
    value: Any
    message: str


@dataclass
class RowResult:
    values: Record
    changed: dict[str, tuple[Any, Any, list[str]]] = field(default_factory=dict)
    flags: list[CellFlag] = field(default_factory=list)
    op_stats: dict[tuple[str, int], list[int]] = field(default_factory=dict)
    llm_keys: list[LLMKey] = field(default_factory=list)


class OpStat(BaseModel):
    op_index: int
    op: str
    changed: int
    flagged: int


def _same_for_diff(a: Any, b: Any, null_equals_empty: bool) -> bool:
    if a == b and type(a) is type(b):
        return True
    if null_equals_empty and a in (None, "") and b in (None, ""):
        return True
    return a == b


def apply_row(
    plan: Plan,
    row: Record,
    resolver: Resolver | None = None,
    *,
    columns: dict[str, ColumnInfo] | None = None,
    null_equals_empty: bool = False,
    llm_max_chars: int | None = None,
) -> RowResult:
    if llm_max_chars is None:
        llm_max_chars = get_settings().llm_max_value_chars
    columns = columns or {}
    res = RowResult(values=dict(row))
    for cp in plan.columns:
        _clean_cell(plan, cp, res, resolver, columns.get(cp.column), null_equals_empty, llm_max_chars)
    extra = row.get(EXTRA_KEY)
    if extra:
        res.flags.append(CellFlag("", None, "", f"Row has {len(extra)} extra field(s)"))
    # flags carry the final value of their cell
    for f in res.flags:
        if f.column:
            f.value = res.values.get(f.column)
    return res


def _clean_cell(plan, cp, res: RowResult, resolver, info: ColumnInfo | None,
                null_equals_empty: bool, llm_max_chars: int) -> None:
    col = cp.column
    row = res.values
    orig = row.get(col)
    value = orig
    sources: list[str] = []
    flags: list[CellFlag] = []
    vflags: list[CellFlag] = []  # flags raised by validate/parse_date (count for if_flagged)
    stats = res.op_stats

    def stat(i: int) -> list[int]:
        return stats.setdefault((col, i), [0, 0])

    def flag(i: int, msg: str, validator: bool = False) -> CellFlag:
        f = CellFlag(col, i, value, msg)
        flags.append(f)
        if validator:
            vflags.append(f)
        stat(i)[1] += 1
        return f

    for i, op in enumerate(cp.ops):
        if not op.enabled:
            continue
        stat(i)
        before = value
        if isinstance(op, LLMFix):
            value = _run_llm_fix(plan, cp, i, op, value, row, res, resolver, info, vflags, flags,
                                 stats, llm_max_chars, flag)
        elif value is not None and not isinstance(value, str):
            continue  # string-only rule
        elif isinstance(op, Validate):
            msg = op_validate(op, value)
            if msg:
                flag(i, msg, True)
        elif isinstance(op, MapFromColumn):
            value, msg = op_map_from_column(op, value, stringify(row.get(op.source_column)), col)
            if msg:
                flag(i, msg)
        elif value is None:
            continue
        elif isinstance(op, ParseDate):
            value, msg = op_parse_date(op, value)
            if msg:
                flag(i, msg, True)
        elif isinstance(op, NullIf):
            value = op_null_if(op, value)
        else:
            value = _PURE[type(op)](op, value)
        if value != before or type(value) is not type(before):
            stat(i)[0] += 1
            if op.op not in sources:
                sources.append(op.op)

    # max_length revert (SQL)
    if (isinstance(value, str) and info is not None and info.max_length is not None
            and len(value) > info.max_length and value != orig):
        value = orig
        sources = []
        flags.append(CellFlag(col, None, orig, f"Result exceeds column length ({info.max_length})"))

    if _same_for_diff(orig, value, null_equals_empty):
        value = orig
    else:
        res.changed[col] = (orig, value, sources)
    row[col] = value
    res.flags.extend(flags)


_PURE: dict[type, Callable[[Any, str], str]] = {
    Trim: op_trim,
    CollapseWhitespace: op_collapse_whitespace,
    Case: op_case,
    RegexReplace: op_regex_replace,
    Reformat: op_reformat,
    Translate: op_translate,
    MapValues: op_map_values,
}


def _run_llm_fix(plan, cp, i: int, op: LLMFix, value, row, res: RowResult, resolver,
                 info: ColumnInfo | None, vflags: list[CellFlag], flags: list[CellFlag],
                 stats, llm_max_chars: int, flag):
    if not isinstance(value, str) or not value.strip():
        return value
    if op.when == "if_flagged":
        triggered = bool(vflags)
    elif op.when == "if_not_match":
        triggered = op.pattern is not None and not _full(op.pattern, value)
    else:
        triggered = True
    if not triggered:
        return value
    if len(value) > llm_max_chars:
        flag(i, "Too long for LLM fix")
        return value
    key = LLMKey(cp.column, i, value,
                 tuple((c, stringify(row.get(c))) for c in op.context_columns))
    res.llm_keys.append(key)
    if resolver is None:
        return value
    ans = resolver(key)
    if ans is None:
        flag(i, "LLM fix not attempted (source changed during run)")
        return value
    if ans.failure:
        flag(i, ans.failure)
        return value
    cand = ans.value
    if not ans.fixed or not isinstance(cand, str) or not cand.strip() or cand == value:
        flag(i, f"LLM could not fix: {ans.reason or 'no usable replacement'}")
        return value
    accepted = _accept(cand, cp.ops, i, info.max_length if info else None)
    if accepted is None:
        flag(i, "LLM fix rejected: result failed validation")
        return value
    # accepted: drop flags raised by validate/parse_date on this cell
    for f in vflags:
        flags.remove(f)
        stats[(cp.column, f.op_index)][1] -= 1
    vflags.clear()
    return accepted


# ------------------------------------------------------------------------ check_plan

_DOLLAR_REF = re.compile(r"\$\d")


def _check_template(c: re.Pattern[str], template: str) -> str | None:
    if _DOLLAR_REF.search(template):
        return "uses a $N backreference; use \\1 (Python syntax)"
    try:
        c.sub(template, "")
    except (re.error, IndexError) as e:
        return f"invalid replacement/template: {e}"
    return None


def check_plan(plan: Plan, columns: list[ColumnInfo]) -> tuple[list[str], list[str]]:
    """Rules R2-R8 and R9 (null_if on NOT NULL); R1 is pydantic's. Returns (errors, warnings)."""
    errors: list[str] = []
    warnings: list[str] = []
    info = {c.name: c for c in columns}
    available = ", ".join(info) or "(none)"
    order = {cp.column: n for n, cp in enumerate(plan.columns)}

    def missing(where: str, name: str) -> None:
        errors.append(f"{where}: column '{name}' does not exist (available columns: {available})")

    seen: set[str] = set()
    for cp in plan.columns:
        if cp.column in seen:
            errors.append(f"column '{cp.column}' appears more than once in the plan")
        seen.add(cp.column)
        if cp.column not in info:
            missing(f"column '{cp.column}'", cp.column)
        elif not info[cp.column].cleanable:
            errors.append(f"column '{cp.column}' is not cleanable (non-text column); it is read-only")
        if sum(1 for o in cp.ops if o.enabled and isinstance(o, LLMFix)) > 1:
            errors.append(f"column '{cp.column}': at most one enabled llm_fix per column")
        for i, op in enumerate(cp.ops):
            where = f"column '{cp.column}' op {i} ({op.op})"
            refs: list[str] = []
            if isinstance(op, MapFromColumn):
                refs = [op.source_column]
            elif isinstance(op, LLMFix):
                refs = list(op.context_columns)
            for r in refs:
                if r not in info:
                    missing(where, r)
            if not op.enabled:
                continue
            _check_op(plan, cp, i, op, where, info.get(cp.column), errors, warnings)
            if isinstance(op, MapFromColumn) and op.source_column in order:
                if order[op.source_column] >= order[cp.column]:
                    errors.append(f"{where}: source_column '{op.source_column}' is planned "
                                  f"{'in the same column' if op.source_column == cp.column else 'after this column'}; "
                                  "it must appear earlier in 'columns'")
    # R7
    fix_cols = {cp.column for cp in plan.columns if any(o.enabled and isinstance(o, LLMFix) for o in cp.ops)}
    for cp in plan.columns:
        for i, op in enumerate(cp.ops):
            if not op.enabled:
                continue
            refs = ([op.source_column] if isinstance(op, MapFromColumn)
                    else list(op.context_columns) if isinstance(op, LLMFix) else [])
            for r in refs:
                if r in fix_cols:
                    errors.append(f"column '{cp.column}' op {i} ({op.op}): reads column '{r}' which has an "
                                  "llm_fix; an llm_fix column cannot be used as source_column/context_columns")
    return errors, warnings


def _control_char(s: str) -> str | None:
    """First control character other than tab/newline/CR (e.g. the \x08 that a JSON "\\b" produces)."""
    for ch in s:
        if (ch < " " and ch not in "\t\n\r") or ch == "\x7f":
            return repr(ch)
    return None


def _check_op(plan, cp, i, op, where, info, errors, warnings) -> None:
    def ctrl(label: str, s: str | None) -> None:
        bad = None if s is None else _control_char(s)
        if bad:
            errors.append(f"{where}: {label} {s!r} contains a control character ({bad}); a JSON "
                          "escape such as \\b, \\f or \\v was probably meant as a regex escape: "
                          "double the backslash in JSON (\\\\b, \\\\d, \\\\1)")

    if isinstance(op, (RegexReplace, Reformat, Validate, LLMFix, NullIf)):
        ctrl("pattern", op.pattern)
    if isinstance(op, RegexReplace):
        ctrl("replacement", op.replacement)
    elif isinstance(op, Reformat):
        ctrl("template", op.template)

    def regex(pattern: str, ic: bool = False) -> re.Pattern[str] | None:
        try:
            return compile_regex(pattern, ic)
        except re.error as e:
            errors.append(f"{where}: invalid regex {pattern!r}: {e}")
            return None

    if isinstance(op, RegexReplace):
        if (c := regex(op.pattern, op.ignore_case)) and (m := _check_template(c, op.replacement)):
            errors.append(f"{where}: replacement {op.replacement!r} {m}")
    elif isinstance(op, Reformat):
        if (c := regex(op.pattern, op.ignore_case)) and (m := _check_template(c, op.template)):
            errors.append(f"{where}: template {op.template!r} {m}")
    elif isinstance(op, Translate):
        for k in op.mapping:
            if len(k) != 1:
                errors.append(f"{where}: translate key {k!r} must be exactly one character")
    elif isinstance(op, NullIf):
        if not op.values and op.pattern is None:
            errors.append(f"{where}: null_if needs 'values' or 'pattern'")
        if op.pattern is not None:
            regex(op.pattern, True)
        if info is not None and not info.nullable:
            errors.append(f"{where}: column '{cp.column}' is NOT NULL; null_if cannot be used on it")
    elif isinstance(op, ParseDate):
        try:
            rendered = datetime(2001, 2, 3).strftime(op.output_format)
            if not rendered:
                errors.append(f"{where}: output_format {op.output_format!r} produces an empty string")
            elif _read_date(op, rendered.strip()) != rendered.strip():
                warnings.append(f"{where}: output_format {op.output_format!r} renders {rendered!r}, "
                                "which parse_date itself would not read back unchanged; "
                                "re-running the plan would alter these dates")
        except (ValueError, TypeError) as e:
            errors.append(f"{where}: invalid output_format {op.output_format!r}: {e}")
    elif isinstance(op, Validate):
        regex(op.pattern, op.ignore_case)
    elif isinstance(op, LLMFix):
        if op.pattern is not None:
            regex(op.pattern)
        if op.when == "if_not_match" and not op.pattern:
            errors.append(f"{where}: when='if_not_match' requires 'pattern'")
        if op.when == "if_flagged" and not any(
                o.enabled and isinstance(o, (Validate, ParseDate)) for o in cp.ops[:i]):
            errors.append(f"{where}: when='if_flagged' requires an enabled validate or parse_date "
                          "earlier in the same column")
        if op.when == "always":
            warnings.append(f"column '{cp.column}' op {i} (llm_fix): when='always' sends every "
                            "non-empty value to the LLM (expensive)")


def check_in_place(plan: Plan, key_column: str | None) -> list[str]:
    """R9, in-place SQL: the key column may not be planned."""
    if key_column and any(cp.column == key_column for cp in plan.columns):
        return [f"in-place run: key column '{key_column}' cannot be cleaned"]
    return []
