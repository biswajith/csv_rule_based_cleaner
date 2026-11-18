"""scan / resolve / write executor with a diff-based change log (spec §6)."""

from __future__ import annotations

import csv
import json
import secrets
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from time import monotonic
from typing import Any, Callable

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

from .config import get_settings
from .connectors.base import ColumnInfo, Connector, SourceError
from .llm import LLM, LLMError
from .ops import (
    Answer, LLMKey, OpStat, apply_row, cell_text, check_in_place, check_plan, llm_op,
    must_match_patterns, target_date_format,
)
from .plan import Approval, LLMFix, Plan
from .planner import PlanError
from .prompts import FIXER_SYSTEM, fixer_user

Progress = Callable[[str, int, int | None], None]
CONSECUTIVE_FAILURES_STOP = 3
CONTEXT_MAX_CHARS = 300


# ----------------------------------------------------------------------- report


class ColumnReport(BaseModel):
    column: str
    cells_changed: int = 0
    cells_flagged: int = 0
    ops: list[OpStat] = []


class LLMReport(BaseModel):
    model: str | None = None
    calls: int = 0
    failed_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cells_triggered: int = 0     # triggered cells seen in the scan (with repeats)
    cells_unique: int = 0        # unique request keys (over-budget keys counted per occurrence)
    cells_sent: int = 0          # unique keys sent in LLM calls
    cells_fixed: int = 0         # cells whose value was changed by llm_fix in the write pass
    cells_over_budget: int = 0


class RunReport(BaseModel):
    run_id: str
    status: str = "done"  # done | failed | cancelled
    error: str | None = None
    source: str = ""
    target: str = ""
    in_place: bool = False
    started_at: str = ""
    finished_at: str = ""
    duration_s: float = 0.0
    rows: int = 0
    rows_written: int = 0
    cells_changed: int = 0
    cells_flagged: int = 0       # distinct flagged cells (row-level flags included)
    flags_written: int = 0       # lines in flags.csv
    columns: list[ColumnReport] = []
    llm: LLMReport = LLMReport()
    approval: Approval | None = None


def new_run_id() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S-") + secrets.token_hex(2)


# ----------------------------------------------------------------------- resolve


def _to_str(v: Any) -> Any:
    return str(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else v


class FixItem(BaseModel):
    """One answer of the fixer. Lenient about what models actually emit (numeric ids/values,
    null reasons, extra keys)."""

    model_config = ConfigDict(extra="ignore")

    id: str
    value: str | None = None
    fixed: bool = False
    reason: str | None = None

    @field_validator("id", "value", mode="before")
    @classmethod
    def _coerce(cls, v: Any) -> Any:
        return _to_str(v)


class FixerResponse(BaseModel):
    # items are validated one by one (see _parse_fix_items): one garbage item must not
    # fail the whole chunk
    results: list[Any] = []


def _parse_fix_items(results: list[Any]) -> list[FixItem]:
    out: list[FixItem] = []
    for r in results:
        if not isinstance(r, dict):
            continue
        try:
            out.append(FixItem.model_validate(r))
        except ValidationError:
            rid = _to_str(r.get("id"))
            if isinstance(rid, str):
                out.append(FixItem(id=rid, fixed=False, reason="malformed answer"))
    return out


def _resolve(plan: Plan, keys: list[LLMKey], llm: LLM, cancel: threading.Event | None,
             progress: Progress | None) -> tuple[dict[LLMKey, Answer], int]:
    """Batched, de-duplicated LLM calls. Returns (answers, number of keys actually sent)."""
    s = get_settings()
    groups: dict[tuple[str, int], list[LLMKey]] = {}
    for k in keys:
        groups.setdefault((k.column, k.op_index), []).append(k)
    chunks: list[tuple[str, int, list[LLMKey]]] = []
    for (col, idx), ks in groups.items():
        for i in range(0, len(ks), s.llm_cells_per_call):
            chunks.append((col, idx, ks[i:i + s.llm_cells_per_call]))

    lock = threading.Lock()
    state = {"consecutive": 0, "dead": False}

    def run(chunk: tuple[str, int, list[LLMKey]]) -> tuple[list[tuple[LLMKey, Answer]], int]:
        col, idx, ks = chunk
        if cancel is not None and cancel.is_set():
            return [], 0
        with lock:
            dead = state["dead"]
        if dead:
            return [(k, Answer(None, False, failure="LLM error: LLM unavailable")) for k in ks], 0
        op = llm_op(plan, col, idx)
        items = [{"id": str(n), "value": k.value,
                  "context": {c: v[:CONTEXT_MAX_CHARS] for c, v in k.context if v}}
                 for n, k in enumerate(ks)]  # context is truncated here, never in the key
        # drop empty context objects to keep prompts small
        for it in items:
            if not it["context"]:
                del it["context"]
        messages = [("system", FIXER_SYSTEM),
                    ("human", fixer_user(col, op.instruction, must_match_patterns(plan, col, idx),
                                         json.dumps(items, ensure_ascii=False),
                                         target_date_format(plan, col, idx)))]
        try:
            resp, _raw = llm.complete_json(messages, FixerResponse)
        except Exception as e:
            with lock:
                state["consecutive"] += 1
                if state["consecutive"] >= CONSECUTIVE_FAILURES_STOP:
                    state["dead"] = True
            msg = (str(e) or type(e).__name__)[:200]
            return [(k, Answer(None, False, failure=f"LLM error: {msg}")) for k in ks], len(ks)
        with lock:
            state["consecutive"] = 0
        by_id: dict[str, FixItem] = {}
        for r in _parse_fix_items(resp.results):
            by_id.setdefault(str(r.id), r)  # unknown ids are never looked up; first duplicate wins
        out: list[tuple[LLMKey, Answer]] = []
        for n, k in enumerate(ks):
            r = by_id.get(str(n))
            out.append((k, Answer(None, False, "no answer") if r is None
                        else Answer(r.value, r.fixed, r.reason or "")))
        return out, len(ks)

    answers: dict[LLMKey, Answer] = {}
    sent = 0
    done = 0
    if progress:
        progress("resolving", 0, len(chunks))
    with ThreadPoolExecutor(max_workers=max(1, s.llm_concurrency)) as pool:
        futures = [pool.submit(run, c) for c in chunks]
        for fut in as_completed(futures):
            res, n = fut.result()
            answers.update(res)
            sent += n
            done += 1
            if progress:
                progress("resolving", done, len(chunks))
    return answers, sent


# ----------------------------------------------------------------------- clean


def clean(connector: Connector, plan: Plan, llm: LLM | None, *, in_place: bool = False,
          overwrite: bool = False, no_llm: bool = False, max_llm_cells: int | None = None,
          run_dir: Path, progress: Progress | None = None,
          cancel: threading.Event | None = None) -> RunReport:
    s = get_settings()
    infos = connector.column_info()
    info_map: dict[str, ColumnInfo] = {c.name: c for c in infos}

    # preconditions: raise before any data is read
    errors, _ = check_plan(plan, infos)
    if in_place:
        errors += check_in_place(plan, getattr(connector, "key_column", None))
    if errors:
        raise PlanError(errors)
    has_llm_fix = plan.has_llm_fix()
    use_llm = has_llm_fix and not no_llm
    if use_llm and llm is None:
        raise LLMError("the plan has llm_fix ops but no LLM is available "
                       "(set OPENAI_API_KEY or run with --no-llm)")
    run_dir.mkdir(parents=True, exist_ok=True)
    writer = connector.writer(run_dir=run_dir, in_place=in_place, overwrite=overwrite)

    llm_slots = {(cp.column, i) for cp in plan.columns for i, o in enumerate(cp.ops)
                 if o.enabled and isinstance(o, LLMFix)}
    cap = s.max_llm_cells if max_llm_cells is None else max_llm_cells
    report = RunReport(run_id=run_dir.name, source=connector.description, target=writer.target,
                       in_place=in_place, llm=LLMReport(model=llm.model_name if llm else None),
                       approval=plan.approval)
    started = datetime.now(timezone.utc)
    t0 = monotonic()
    (run_dir / "plan.json").write_text(plan.model_dump_json(indent=2), encoding="utf-8")

    col_changed: dict[str, int] = {}
    col_flagged: dict[str, int] = {}
    op_totals: dict[tuple[str, int], list[int]] = {}
    total_rows = connector.count()
    cancelled = False
    uncommitted = 0

    def is_cancelled() -> bool:
        return cancel is not None and cancel.is_set()

    try:
        with writer, \
                (run_dir / "changes.csv").open("w", newline="", encoding="utf-8") as cf, \
                (run_dir / "flags.csv").open("w", newline="", encoding="utf-8") as ff:
            changes = csv.writer(cf)
            flags = csv.writer(ff)
            changes.writerow(["row_key", "column", "before", "after", "sources"])
            flags.writerow(["row_key", "column", "value", "message"])
            cf.flush()
            ff.flush()

            # ---- scan
            keys: dict[LLMKey, None] = {}
            over_budget = 0
            triggered = 0
            if use_llm:
                done = 0
                for batch in connector.iter_batches(s.batch_size):
                    if is_cancelled():
                        cancelled = True
                        break
                    for _rk, rec in batch:
                        res = apply_row(plan, rec, None, columns=info_map,
                                        null_equals_empty=connector.null_equals_empty)
                        for k in res.llm_keys:
                            triggered += 1
                            if k in keys:
                                continue
                            if len(keys) >= cap:
                                over_budget += 1
                            else:
                                keys[k] = None
                    done += len(batch)
                    if progress:
                        progress("scanning", done, total_rows)
            report.llm.cells_triggered = triggered
            report.llm.cells_unique = len(keys) + over_budget
            report.llm.cells_over_budget = over_budget

            # ---- resolve
            answers: dict[LLMKey, Answer] = {}
            if use_llm and keys and not cancelled:
                assert llm is not None
                answers, sent = _resolve(plan, list(keys), llm, cancel, progress)
                report.llm.cells_sent = sent
                cancelled = is_cancelled()

            def resolver(key: LLMKey) -> Answer | None:
                if no_llm:
                    return Answer(None, False, failure="LLM skipped")
                ans = answers.get(key)
                if ans is not None:
                    return ans
                if over_budget:
                    return Answer(None, False, failure="LLM budget exceeded")
                return None

            # ---- write
            # Per batch: build every log row in memory; for in-place targets the change rows
            # (the undo log) go to disk *before* the commit, for new targets they are appended
            # only after the write succeeded; counters move only after the write succeeded.
            if not cancelled:
                rows_done = 0
                for batch in connector.iter_batches(s.batch_size):
                    if is_cancelled():
                        cancelled = True
                        break
                    out = []
                    change_rows: list[list[str]] = []
                    flag_rows: list[list[str]] = []
                    d_changed: dict[str, int] = {}
                    d_flagged: dict[str, int] = {}
                    d_ops: dict[tuple[str, int], list[int]] = {}
                    d_fixed = 0
                    for rk, rec in batch:
                        res = apply_row(plan, rec, resolver if has_llm_fix else None,
                                        columns=info_map,
                                        null_equals_empty=connector.null_equals_empty)
                        out.append((rk, res.values, set(res.changed)))
                        for col, (before, after, sources) in res.changed.items():
                            change_rows.append([rk, col, cell_text(before), cell_text(after),
                                                ";".join(sources)])
                            d_changed[col] = d_changed.get(col, 0) + 1
                        for f in res.flags:
                            flag_rows.append([rk, f.column, cell_text(f.value), f.message])
                        for col in {f.column for f in res.flags}:
                            d_flagged[col] = d_flagged.get(col, 0) + 1
                        for k2, (c, fl) in res.op_stats.items():
                            t = d_ops.setdefault(k2, [0, 0])
                            t[0] += c
                            t[1] += fl
                            if c and k2 in llm_slots:
                                d_fixed += c
                    assert len(out) == len(batch), "rows read != rows handed to the writer"
                    if in_place and change_rows:
                        changes.writerows(change_rows)
                        cf.flush()
                        uncommitted = len(change_rows)
                    writer.write(out)
                    uncommitted = 0
                    if not in_place:
                        changes.writerows(change_rows)
                    flags.writerows(flag_rows)
                    cf.flush()
                    ff.flush()
                    for col, n in d_changed.items():
                        col_changed[col] = col_changed.get(col, 0) + n
                    for col, n in d_flagged.items():
                        col_flagged[col] = col_flagged.get(col, 0) + n
                    for k2, (c, fl) in d_ops.items():
                        t = op_totals.setdefault(k2, [0, 0])
                        t[0] += c
                        t[1] += fl
                    report.llm.cells_fixed += d_fixed
                    report.flags_written += len(flag_rows)
                    report.rows += len(batch)
                    report.rows_written += len(out)
                    rows_done += len(batch)
                    if progress:
                        progress("writing", rows_done, total_rows)
                if not cancelled and total_rows is not None and report.rows != total_rows:
                    raise SourceError(f"read {report.rows} rows but the source count is "
                                      f"{total_rows}: the source changed during the run or the "
                                      "connector skipped rows")
        report.status = "cancelled" if cancelled else "done"
    except Exception as e:  # recorded, not raised: callers inspect report.status
        report.status = "failed"
        report.error = str(e) or type(e).__name__
        if uncommitted:
            report.error += (f" (in-place: the last {uncommitted} change row(s) in changes.csv "
                             "belong to a batch that was not committed)")

    _finish(report, plan, llm, started, t0, col_changed, col_flagged, op_totals)
    (run_dir / "report.json").write_text(report.model_dump_json(indent=2), encoding="utf-8")
    return report


def _finish(report: RunReport, plan: Plan, llm: LLM | None, started: datetime, t0: float,
           col_changed: dict[str, int], col_flagged: dict[str, int],
           op_totals: dict[tuple[str, int], list[int]]) -> None:
    report.cells_changed = sum(col_changed.values())
    report.cells_flagged = sum(col_flagged.values())
    report.columns = []
    for cp in plan.columns:
        ops = [OpStat(op_index=i, op=op.op, changed=op_totals.get((cp.column, i), [0, 0])[0],
                      flagged=op_totals.get((cp.column, i), [0, 0])[1])
               for i, op in enumerate(cp.ops) if op.enabled]
        report.columns.append(ColumnReport(column=cp.column,
                                           cells_changed=col_changed.get(cp.column, 0),
                                           cells_flagged=col_flagged.get(cp.column, 0), ops=ops))
    if llm is not None:
        u = llm.usage
        report.llm.calls = u.calls
        report.llm.failed_calls = u.failed_calls
        report.llm.input_tokens = u.input_tokens
        report.llm.output_tokens = u.output_tokens
    report.started_at = started.isoformat(timespec="seconds")
    report.finished_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    report.duration_s = round(monotonic() - t0, 3)
