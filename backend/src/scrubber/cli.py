"""Typer CLI (spec §9). Exit codes: 0 ok, 1 failure, 2 usage error, 130 cancelled."""

from __future__ import annotations

import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from pathlib import Path
from typing import Annotated, Optional

import typer
from pydantic import ValidationError
from rich.console import Console
from rich.markup import escape
from rich.progress import BarColumn, Progress, TaskProgressColumn, TextColumn, TimeElapsedColumn
from rich.table import Table

from . import planner
from .cleaner import RunReport, clean, new_run_id
from .config import get_settings
from .connectors import (
    Connector,
    CSVSource,
    MongoSource,
    OutputExistsError,
    SourceError,
    SQLSource,
    open_source,
)
from .llm import LLMError, make_llm
from .ops import check_in_place, check_plan
from .plan import Plan
from .planner import PlanError, PlanResult, Preview

app = typer.Typer(add_completion=False, no_args_is_help=True,
                  help="Scrubber: LLM-planned, deterministic data cleaning.")
out = Console()
err = Console(stderr=True)

LOOPBACK = {"127.0.0.1", "localhost", "::1"}

Source = Annotated[str, typer.Argument(help="CSV path, SQL URL, mongodb:// URI or env:NAME")]
Delimiter = Annotated[str, typer.Option("--delimiter", help="CSV delimiter")]
Encoding = Annotated[str, typer.Option("--encoding", help="CSV encoding")]
Table_ = Annotated[Optional[str], typer.Option("--table", help="SQL table")]
Schema = Annotated[Optional[str], typer.Option("--schema", help="SQL schema")]
KeyColumn = Annotated[Optional[str], typer.Option("--key-column", help="SQL unique, non-null key")]
Db = Annotated[Optional[str], typer.Option("--db", help="MongoDB database")]
Collection = Annotated[Optional[str], typer.Option("--collection", help="MongoDB collection")]
Filter = Annotated[Optional[str], typer.Option("--filter", help="MongoDB filter (JSON)")]
Columns = Annotated[Optional[str], typer.Option("--columns", help="Only plan these columns (a,b,c)")]
InPlace = Annotated[bool, typer.Option("--in-place", help="Modify the source (SQL/MongoDB)")]
Overwrite = Annotated[bool, typer.Option("--overwrite", help="Replace an existing output")]
NoLLM = Annotated[bool, typer.Option("--no-llm", help="Never call the LLM (llm_fix cells are flagged)")]
MaxCells = Annotated[Optional[int], typer.Option("--max-llm-cells", help="Cap on LLM cells")]
OutDir = Annotated[Optional[Path], typer.Option("--out", help="Output base directory")]
Yes = Annotated[bool, typer.Option("--yes", "-y", help="Skip confirmations")]


def fail(msg: str, code: int = 1) -> "typer.Exit":
    err.print(escape(msg), style="red", highlight=False)
    return typer.Exit(code)


def usage(msg: str) -> "typer.Exit":
    return fail(msg, 2)


# ----------------------------------------------------------------------- source


def build_spec(source: str, *, delimiter: str, encoding: str, table: str | None,
               schema: str | None, key_column: str | None, db: str | None,
               collection: str | None, filter_: str | None) -> CSVSource | SQLSource | MongoSource:
    if source.startswith("env:"):
        name = source[4:]
        val = os.environ.get(name)
        if not val:
            raise usage(f"environment variable {name} is not set")
        source = val
    if source.startswith(("mongodb://", "mongodb+srv://")):
        if not db or not collection:
            raise usage("MongoDB sources need --db and --collection")
        flt = {}
        if filter_:
            try:
                flt = json.loads(filter_)
            except ValueError as e:
                raise usage(f"--filter is not valid JSON: {e}") from e
            if not isinstance(flt, dict):
                raise usage("--filter must be a JSON object")
        return MongoSource(uri=source, database=db, collection=collection, filter=flt)
    if "://" in source:
        if not table:
            raise usage("SQL sources need --table")
        return SQLSource(url=source, table=table, schema=schema, key_column=key_column)
    return CSVSource(path=source, delimiter="\t" if delimiter == "\\t" else delimiter,
                     encoding=encoding)


def open_or_exit(spec: CSVSource | SQLSource | MongoSource) -> Connector:
    try:
        return open_source(spec)
    except SourceError as e:
        raise fail(f"source error: {e}") from e


def read_rules(path: Path) -> list[str]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as e:
        raise usage(f"cannot read rules file: {e}") from e
    except UnicodeDecodeError as e:
        raise usage(f"rules file is not UTF-8: {e}") from e
    rules = [ln.strip() for ln in lines if ln.strip() and not ln.strip().startswith("#")]
    if not rules:
        raise usage(f"no rules found in {path}")
    return rules


def split_columns(columns: str | None) -> list[str] | None:
    if columns is None:
        return None
    cols = [c.strip() for c in columns.split(",") if c.strip()]
    if not cols:
        raise usage("--columns is empty")
    return cols


# ----------------------------------------------------------------------- rendering


def show_profile(prof) -> None:
    out.print(f"Source: {escape(prof.source)}   rows: {prof.total_rows}   sampled: {prof.sampled_rows}")
    t = Table("column", "type", "empty", "distinct", "top shapes", "cleanable")
    for c in prof.columns:
        t.add_row(escape(c.name), c.inferred_type, str(c.empty), str(c.distinct_count),
                  escape(", ".join(f"{s.shape} ×{s.count}" for s in c.shapes[:3])),
                  "yes" if c.cleanable else "read-only")
    out.print(t)


def _op_summary(op) -> str:
    d = op.model_dump(exclude={"op", "enabled", "note"}, exclude_defaults=True)
    s = ", ".join(f"{k}={json.dumps(v, ensure_ascii=False)}" for k, v in d.items())
    return s if len(s) <= 90 else s[:89] + "…"


def show_plan(plan: Plan) -> None:
    t = Table("column", "#", "op", "summary", title="Plan")
    for cp in plan.columns:
        for i, op in enumerate(cp.ops):
            t.add_row(escape(cp.column) if i == 0 else "", str(i),
                      op.op + ("" if op.enabled else " (disabled)"), escape(_op_summary(op)))
    out.print(t)
    for cp in plan.columns:
        if cp.rationale:
            out.print(f"[bold]{escape(cp.column)}[/bold]: {escape(cp.rationale)}")
    for n in plan.notes:
        out.print(f"Note: {escape(n)}", style="yellow")


def show_preview(pv: Preview) -> None:
    t = Table("column", "changed", "flagged", "per op (changed/flagged)",
              title=f"Preview on {pv.sampled_rows} sampled of {pv.total_rows} rows")
    for c in pv.columns:
        t.add_row(escape(c.column), str(c.cells_changed), str(c.cells_flagged),
                  escape("  ".join(f"{o.op_index}:{o.op} {o.changed}/{o.flagged}" for o in c.ops)))
    out.print(t)
    for c in pv.columns:
        for b, a in c.examples[:3]:
            out.print(f"  {escape(c.column)}: {escape(repr(b))} → {escape(repr(a))}")
        for v, m in c.flag_examples[:3]:
            out.print(f"  {escape(c.column)}: flag {escape(repr(v))} ({escape(m)})")
    for w in pv.warnings:
        out.print(f"Warning: {escape(w)}", style="yellow")
    if pv.llm_cells_sample:
        est = (f"up to ~{pv.llm_cells_estimate} cells may go to the LLM "
               f"({pv.llm_unique_sample} unique in the sample of {pv.sampled_rows})"
               if pv.llm_cells_estimate is not None else
               f"{pv.llm_cells_sample} cells in the sample ({pv.llm_unique_sample} unique)")
        out.print(f"LLM: {est}, capped at {get_settings().max_llm_cells} per run.")


def show_summary(rep: RunReport, run_dir: Path) -> None:
    t = Table("", "", show_header=False, title=f"Run {rep.run_id}: {rep.status}")
    t.add_row("rows", f"{rep.rows} read, {rep.rows_written} written")
    t.add_row("cells changed", str(rep.cells_changed))
    t.add_row("cells flagged", str(rep.cells_flagged))
    l = rep.llm
    t.add_row("LLM", f"{l.cells_sent} cells sent, {l.cells_fixed} fixed, {l.calls} calls "
                     f"({l.failed_calls} failed), {l.input_tokens} in / {l.output_tokens} out tokens")
    t.add_row("target", escape(rep.target))
    t.add_row("run dir", escape(str(run_dir)))
    out.print(t)
    if rep.error:
        err.print(f"error: {escape(rep.error)}", style="red")
        if rep.in_place:
            err.print(f"{rep.rows_written} rows were already written in place; "
                      "changes.csv is the undo record.", style="red")


# ----------------------------------------------------------------------- commands


@app.command("profile")
def profile_cmd(source: Source, delimiter: Delimiter = ",", encoding: Encoding = "utf-8-sig",
                table: Table_ = None, schema: Schema = None, key_column: KeyColumn = None,
                db: Db = None, collection: Collection = None, filter: Filter = None) -> None:
    """Profile a source (no LLM)."""
    spec = build_spec(source, delimiter=delimiter, encoding=encoding, table=table, schema=schema,
                      key_column=key_column, db=db, collection=collection, filter_=filter)
    with open_or_exit(spec) as conn:
        show_profile(planner.profile(conn))


def do_plan(conn: Connector, rules: list[str], columns: list[str] | None) -> PlanResult:
    try:
        llm = make_llm(planner=True)
        res = planner.plan(conn, rules, llm, columns)
    except ValueError as e:
        raise usage(str(e)) from e
    except LLMError as e:
        raise fail(f"LLM error: {e}") from e
    except PlanError as e:
        raise fail("the planner could not produce a valid plan:\n  - " + "\n  - ".join(e.errors)) from e
    show_plan(res.plan)
    show_preview(res.preview)
    u = res.usage
    out.print(f"Planning LLM usage: {u['calls']} calls, {u['input_tokens']} in / "
              f"{u['output_tokens']} out tokens")
    return res


@app.command("plan")
def plan_cmd(source: Source, rules: Annotated[Path, typer.Option("--rules", help="Rules file")],
             output: Annotated[Path, typer.Option("-o", "--output", help="Plan file")] = Path("plan.json"),
             columns: Columns = None, delimiter: Delimiter = ",", encoding: Encoding = "utf-8-sig",
             table: Table_ = None, schema: Schema = None, key_column: KeyColumn = None,
             db: Db = None, collection: Collection = None, filter: Filter = None) -> None:
    """Generate a plan with the LLM, show a dry-run preview and write the plan JSON."""
    spec = build_spec(source, delimiter=delimiter, encoding=encoding, table=table, schema=schema,
                      key_column=key_column, db=db, collection=collection, filter_=filter)
    rule_list = read_rules(rules)
    with open_or_exit(spec) as conn:
        res = do_plan(conn, rule_list, split_columns(columns))
    output.write_text(res.plan.model_dump_json(indent=2), encoding="utf-8")
    out.print(f"Plan written to {escape(str(output))}")


def load_plan(path: Path) -> Plan:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        raise usage(f"cannot read plan file: {e}") from e
    try:
        return Plan.model_validate_json(text)
    except ValidationError as e:
        msgs = [f"{'.'.join(map(str, x['loc']))}: {x['msg']}" for x in e.errors()]
        raise fail("invalid plan:\n  - " + "\n  - ".join(msgs)) from e


def do_apply(spec, conn: Connector, plan: Plan, *, in_place: bool, overwrite: bool, no_llm: bool,
             max_llm_cells: int | None, out_dir: Path | None, yes: bool) -> None:
    errors, _ = check_plan(plan, conn.column_info())
    if in_place:
        errors += check_in_place(plan, getattr(conn, "key_column", None))
    if errors:
        raise fail("invalid plan for this source:\n  - " + "\n  - ".join(errors))
    if in_place and not yes and not isinstance(spec, CSVSource):
        name = spec.table if isinstance(spec, SQLSource) else spec.collection
        err.print(f"This modifies {escape(conn.description)} in place.", style="bold red")
        typed = typer.prompt(f"Type '{name}' to confirm", default="", show_default=False)
        if typed != name:
            raise fail("aborted: confirmation did not match", 1)
    llm = None
    if plan.has_llm_fix() and not no_llm:
        try:
            llm = make_llm()
        except LLMError as e:
            raise fail(f"LLM error: {e}") from e

    base = out_dir if out_dir is not None else Path(get_settings().output_dir)
    run_dir = base / new_run_id()
    cancel = threading.Event()
    progress = Progress(TextColumn("{task.description}"), BarColumn(), TaskProgressColumn(),
                        TimeElapsedColumn(), console=out, transient=True)
    task = {"id": None, "phase": None}

    def on_progress(phase: str, done: int, total: int | None) -> None:
        if task["phase"] != phase:
            if task["id"] is not None:
                progress.remove_task(task["id"])
            task["id"] = progress.add_task(phase, total=total)
            task["phase"] = phase
        progress.update(task["id"], completed=done, total=total)

    pool = ThreadPoolExecutor(max_workers=1)
    with progress:
        fut = pool.submit(clean, conn, plan, llm, in_place=in_place, overwrite=overwrite,
                          no_llm=no_llm, max_llm_cells=max_llm_cells, run_dir=run_dir,
                          progress=on_progress, cancel=cancel)
        while True:
            try:
                rep = fut.result(timeout=0.2)
                break
            except FutureTimeout:
                continue
            except KeyboardInterrupt:
                cancel.set()
                err.print("cancelling…", style="yellow")
            except (PlanError, LLMError, SourceError) as e:  # raised before any data was read
                pool.shutdown(wait=False)
                if isinstance(e, PlanError):
                    raise fail("invalid plan:\n  - " + "\n  - ".join(e.errors)) from e
                raise fail(f"error: {e}") from e
    pool.shutdown(wait=False)
    show_summary(rep, run_dir)
    if rep.status == "failed":
        raise typer.Exit(1)
    if rep.status == "cancelled":
        raise typer.Exit(130)


@app.command("apply")
def apply_cmd(source: Source, plan: Annotated[Path, typer.Option("--plan", help="Plan JSON file")],
              in_place: InPlace = False, overwrite: Overwrite = False, no_llm: NoLLM = False,
              max_llm_cells: MaxCells = None, out_dir: OutDir = None, yes: Yes = False,
              delimiter: Delimiter = ",", encoding: Encoding = "utf-8-sig", table: Table_ = None,
              schema: Schema = None, key_column: KeyColumn = None, db: Db = None,
              collection: Collection = None, filter: Filter = None) -> None:
    """Apply a plan file to a source."""
    spec = build_spec(source, delimiter=delimiter, encoding=encoding, table=table, schema=schema,
                      key_column=key_column, db=db, collection=collection, filter_=filter)
    plan_obj = load_plan(plan)
    with open_or_exit(spec) as conn:
        do_apply(spec, conn, plan_obj, in_place=in_place, overwrite=overwrite, no_llm=no_llm,
                 max_llm_cells=max_llm_cells, out_dir=out_dir, yes=yes)


@app.command("run")
def run_cmd(source: Source, rules: Annotated[Path, typer.Option("--rules", help="Rules file")],
            yes: Yes = False, columns: Columns = None, in_place: InPlace = False,
            overwrite: Overwrite = False, no_llm: NoLLM = False, max_llm_cells: MaxCells = None,
            out_dir: OutDir = None, delimiter: Delimiter = ",", encoding: Encoding = "utf-8-sig",
            table: Table_ = None, schema: Schema = None, key_column: KeyColumn = None,
            db: Db = None, collection: Collection = None, filter: Filter = None) -> None:
    """Plan, show the preview, confirm, then apply."""
    spec = build_spec(source, delimiter=delimiter, encoding=encoding, table=table, schema=schema,
                      key_column=key_column, db=db, collection=collection, filter_=filter)
    rule_list = read_rules(rules)
    with open_or_exit(spec) as conn:
        res = do_plan(conn, rule_list, split_columns(columns))
        if not yes and not typer.confirm("Proceed?", default=False):
            raise typer.Exit(1)
        do_apply(spec, conn, res.plan, in_place=in_place, overwrite=overwrite, no_llm=no_llm,
                 max_llm_cells=max_llm_cells, out_dir=out_dir, yes=yes)


@app.command("serve")
def serve_cmd(host: Annotated[str, typer.Option("--host")] = "127.0.0.1",
              port: Annotated[int, typer.Option("--port")] = 8000,
              allow_remote: Annotated[bool, typer.Option("--allow-remote")] = False) -> None:
    """Serve the web UI and API."""
    if host not in LOOPBACK and not allow_remote:
        raise usage(f"refusing to bind {host}: pass --allow-remote to listen on a non-loopback "
                    "address")
    if allow_remote:
        err.print("WARNING: --allow-remote: the server has NO authentication; anyone who can "
                  "reach it can read uploaded data, connect to databases and spend your API "
                  "budget.", style="bold red")
    import uvicorn

    from .api import create_app

    uvicorn.run(create_app(port=port, allow_remote=allow_remote), host=host, port=port,
                log_level="info")


if __name__ == "__main__":
    app()
