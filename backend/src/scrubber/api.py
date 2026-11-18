"""FastAPI app for the web UI (spec §10, §10.1). Plain `def` endpoints; runs on a worker thread."""

from __future__ import annotations

import csv
import os
import re
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any, Callable

from fastapi import Body, FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel, Field, ValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import planner
from .config import get_settings
from .connectors import (
    ColumnInfo,
    Connector,
    CSVSource,
    MongoSource,
    OutputExistsError,
    SourceError,
    SQLSource,
)
from .llm import LLM, LLMError, make_llm
from .ops import check_in_place, check_plan
from .plan import Plan, approval_error
from .planner import PlanError
from .runs import (
    RUN_ID_RE,
    RunRegistry,
    RunStatus,
    SourceEntry,
    SourceRegistry,
    execute_run,
    list_run_files,
    output_root,
    read_report,
)

DEV_ORIGIN = "http://localhost:5173"
CHUNK = 1024 * 1024


# ----------------------------------------------------------------------- helpers


def _err(status: int, detail: str, errors: list[str] | None = None) -> JSONResponse:
    body: dict[str, Any] = {"detail": detail}
    if errors:
        body["errors"] = errors
    return JSONResponse(body, status_code=status)


def _fmt_validation(e: ValidationError | RequestValidationError) -> list[str]:
    out = []
    for err in e.errors():
        loc = ".".join(str(p) for p in err["loc"] if p != "body")
        out.append(f"{loc}: {err['msg']}" if loc else str(err["msg"]))
    return out


def _parse_plan(raw: dict[str, Any]) -> Plan:
    try:
        return Plan.model_validate(raw)
    except ValidationError as e:
        errs = _fmt_validation(e)
        raise PlanError(errs) from e


def _safe_name(name: str | None) -> str:
    base = Path((name or "").replace("\\", "/")).name
    base = re.sub(r"[^A-Za-z0-9._-]", "_", base).lstrip(".")
    return base or "upload.csv"


def has_api_key() -> bool:
    s = get_settings()
    return bool(s.openai_api_key or os.environ.get("OPENAI_API_KEY"))


class SourceInfo(BaseModel):
    source_id: str
    kind: str
    description: str
    columns: list[ColumnInfo]
    count: int | None
    can_in_place: bool


class PlanRequest(BaseModel):
    source_id: str
    rules: list[str]
    columns: list[str] | None = None


class PreviewRequest(BaseModel):
    source_id: str
    plan: dict[str, Any]


class ApproveRequest(BaseModel):
    plan: dict[str, Any]
    by: str


class RunRequest(BaseModel):
    source_id: str
    plan: dict[str, Any]
    in_place: bool = False
    overwrite: bool = False
    no_llm: bool = False
    max_llm_cells: int | None = Field(default=None, ge=0)


# ----------------------------------------------------------------------- app


def create_app(*, port: int, allow_remote: bool = False, dev: bool | None = None,
               llm_factory: Callable[[bool], LLM] | None = None,
               mongo_client: Any = None) -> FastAPI:
    if dev is None:
        dev = os.environ.get("SCRUBBER_DEV") == "1"
    make = llm_factory or (lambda planner_: make_llm(planner=planner_))
    sources = SourceRegistry(mongo_client=mongo_client)
    runs = RunRegistry()

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        yield
        runs.shutdown()

    app = FastAPI(title="Scrubber", lifespan=lifespan, docs_url=None, redoc_url=None,
                  openapi_url=None)
    app.state.sources = sources
    app.state.runs = runs

    allowed_hosts = {f"{h}:{p}" for h in ("127.0.0.1", "localhost", "[::1]")
                     for p in ([port, 5173] if dev else [port])}

    # ---- security (§10.1): no CORS headers anywhere; Host + Origin checks on /api/*
    @app.middleware("http")
    async def security(request: Request, call_next):
        if request.url.path.startswith("/api/"):
            host = request.headers.get("host", "")
            if not allow_remote and host not in allowed_hosts:
                return _err(403, "Forbidden: unexpected Host header")
            origin = request.headers.get("origin")
            if origin is not None:
                ok = {f"http://{host}", f"https://{host}"}
                if dev:
                    ok.add(DEV_ORIGIN)
                if origin not in ok:
                    return _err(403, "Forbidden: cross-origin request")
            # Reject oversized bodies by declared length before any form parsing spools them
            # to disk; the streaming check in the upload endpoint is the backstop.
            cl = request.headers.get("content-length")
            max_mb = get_settings().max_upload_mb
            if cl and cl.isdigit() and int(cl) > max_mb * 1024 * 1024 + CHUNK:
                return _err(413, f"File too large (limit {max_mb} MB)")
        return await call_next(request)

    # ---- error mapping: body is always {detail, errors?}
    @app.exception_handler(StarletteHTTPException)
    async def _http(_r: Request, e: StarletteHTTPException):
        return _err(e.status_code, str(e.detail))

    @app.exception_handler(RequestValidationError)
    async def _validation(_r: Request, e: RequestValidationError):
        return _err(422, "Invalid request", _fmt_validation(e))

    @app.exception_handler(OutputExistsError)
    async def _exists(_r: Request, e: OutputExistsError):
        return _err(409, f"{e} (retry with overwrite to replace it)")

    @app.exception_handler(SourceError)
    async def _source(_r: Request, e: SourceError):
        return _err(400, str(e))

    @app.exception_handler(PlanError)
    async def _plan(_r: Request, e: PlanError):
        return _err(422, "Invalid plan", e.errors)

    @app.exception_handler(LLMError)
    async def _llm(_r: Request, e: LLMError):
        return _err(502, f"LLM error: {e}")

    @app.exception_handler(Exception)
    async def _unexpected(_r: Request, _e: Exception):
        return _err(500, "Internal server error")

    # ---- helpers needing registries
    def entry_or_404(source_id: str) -> SourceEntry:
        entry = sources.get(source_id)
        if entry is None:
            raise HTTPException(404, "Unknown source (the server may have restarted); reconnect")
        return entry

    def make_llm_or_400(planner_: bool) -> LLM:
        try:
            return make(planner_)
        except LLMError as e:  # construction failure = configuration problem (missing key, ...)
            raise HTTPException(400, f"LLM not available: {e}") from e

    def register(spec: CSVSource | SQLSource | MongoSource) -> SourceInfo:
        conn = sources.open(spec)
        try:
            columns = conn.column_info()
            entry = sources.add(spec, conn.description, columns)
            return SourceInfo(source_id=entry.source_id, kind=spec.kind,
                              description=conn.description, columns=columns,
                              count=conn.count(), can_in_place=entry.can_in_place)
        finally:
            conn.close()

    # ------------------------------------------------------------------ endpoints

    @app.get("/api/config")
    def config() -> dict[str, Any]:
        s = get_settings()
        return {"model": s.llm_model, "has_api_key": has_api_key(), "sample_size": s.sample_size,
                "max_llm_cells": s.max_llm_cells, "max_upload_mb": s.max_upload_mb}

    @app.post("/api/uploads")
    def upload(file: UploadFile = File(...), delimiter: str = Form(","),
               encoding: str = Form("utf-8-sig")) -> SourceInfo:
        s = get_settings()
        limit = s.max_upload_mb * 1024 * 1024
        delim = "\t" if delimiter.lower() in ("\\t", "tab") else delimiter
        if len(delim) != 1:
            raise HTTPException(400, "Delimiter must be a single character")
        upload_dir = Path(s.upload_dir)
        upload_dir.mkdir(parents=True, exist_ok=True)
        dest = upload_dir / f"{uuid.uuid4().hex}.csv"
        size = 0
        try:
            with dest.open("wb") as out:
                while chunk := file.file.read(CHUNK):
                    size += len(chunk)
                    if size > limit:
                        raise HTTPException(413, f"File too large (limit {s.max_upload_mb} MB)")
                    out.write(chunk)
            spec = CSVSource(path=str(dest), delimiter=delim, encoding=encoding or "utf-8-sig",
                             display_name=_safe_name(file.filename))
            return register(spec)
        except BaseException:
            dest.unlink(missing_ok=True)
            raise

    @app.post("/api/sources")
    def add_source(spec: Annotated[SQLSource | MongoSource, Body(discriminator="kind")]) -> SourceInfo:
        return register(spec)

    @app.get("/api/sources/{source_id}/profile")
    def profile(source_id: str) -> planner.Profile:
        entry = entry_or_404(source_id)
        with sources.open_entry(entry) as conn:
            return planner.profile(conn)

    @app.post("/api/plan")
    def make_plan(req: PlanRequest) -> planner.PlanResult:
        entry = entry_or_404(req.source_id)
        with sources.open_entry(entry) as conn:
            llm = make_llm_or_400(True)
            try:
                return planner.plan(conn, req.rules, llm, req.columns)
            except ValueError as e:
                raise HTTPException(400, str(e)) from e

    @app.post("/api/preview")
    def preview(req: PreviewRequest) -> dict[str, Any]:
        entry = entry_or_404(req.source_id)
        plan = _parse_plan(req.plan)
        stale = plan.approval if plan.approval is not None and not plan.is_approved() else None
        if stale is not None:
            plan = plan.model_copy(update={"approval": None})
        with sources.open_entry(entry) as conn:
            pv = planner.preview(conn, plan)
        if stale is not None:
            pv.warnings.append(f"Approval by {stale.by} was removed: the plan changed after it "
                               "was approved.")
        return {"plan": plan.model_dump(mode="json"), "preview": pv}

    @app.post("/api/plans/approve")
    def approve(req: ApproveRequest) -> dict[str, Any]:
        plan = _parse_plan(req.plan)
        try:
            approved = plan.with_approval(req.by)
        except ValueError as e:
            return _err(422, str(e))
        return {"plan": approved.model_dump(mode="json")}

    @app.post("/api/runs")
    def start_run(req: RunRequest) -> Any:
        entry = entry_or_404(req.source_id)
        plan = _parse_plan(req.plan)
        if (msg := approval_error(plan)) is not None:
            return _err(409, msg)
        if req.in_place and not entry.can_in_place:
            raise HTTPException(400, "In-place cleaning is not supported for CSV sources")
        # Preconditions are checked here so the client gets 400/409/422/502 immediately;
        # `clean` re-checks them when the run actually starts.
        with sources.open_entry(entry) as conn:
            errors, _ = check_plan(plan, conn.column_info())
            if req.in_place:
                errors += check_in_place(plan, getattr(conn, "key_column", None))
            if errors:
                raise PlanError(errors)
            conn.check_target(in_place=req.in_place, overwrite=req.overwrite)
        llm = make_llm_or_400(False) if plan.has_llm_fix() and not req.no_llm else None
        handle = runs.new_handle()
        runs.submit(handle, lambda h: execute_run(
            runs, h, lambda: sources.open_entry(entry), plan, llm, in_place=req.in_place,
            overwrite=req.overwrite, no_llm=req.no_llm, max_llm_cells=req.max_llm_cells))
        return {"run_id": handle.run_id}

    def run_or_404(run_id: str) -> RunStatus:
        st = runs.status(run_id)
        if st is None:
            raise HTTPException(404, "Unknown run")
        return st

    @app.get("/api/runs/{run_id}")
    def get_run(run_id: str) -> RunStatus:
        return run_or_404(run_id)

    @app.post("/api/runs/{run_id}/cancel")
    def cancel_run(run_id: str) -> RunStatus:
        run_or_404(run_id)
        runs.cancel(run_id)
        return run_or_404(run_id)

    def page(run_id: str, name: str, column: str, offset: int, limit: int,
             total_key: str, build: Callable[[list[str]], dict[str, Any]]) -> dict[str, Any]:
        st = run_or_404(run_id)
        path = output_root() / run_id / name
        if not path.is_file():  # queued-then-cancelled / precondition failure: nothing was logged
            return {"total": 0, "items": []}
        limit = max(1, min(limit, 1000))
        known_total = None if column or st.report is None else st.report.get(total_key)
        items: list[dict[str, Any]] = []
        matched = 0
        with path.open(newline="", encoding="utf-8") as f:
            r = csv.reader(f)
            next(r, None)
            for row in r:
                if column and (len(row) < 2 or row[1] != column):
                    continue
                if offset <= matched < offset + limit:
                    items.append(build(row))
                matched += 1
                if known_total is not None and matched >= offset + limit:
                    break  # total comes from the report: no need to scan the rest
        return {"total": matched if known_total is None else known_total, "items": items}

    @app.get("/api/runs/{run_id}/changes")
    def changes(run_id: str, column: str = "", offset: int = Query(0, ge=0),
                limit: int = 100) -> dict[str, Any]:
        def build(r: list[str]) -> dict[str, Any]:
            r = r + [""] * (5 - len(r))
            return {"row_key": r[0], "column": r[1], "before": r[2], "after": r[3],
                    "sources": [x for x in r[4].split(";") if x]}
        return page(run_id, "changes.csv", column, offset, limit, "cells_changed", build)

    @app.get("/api/runs/{run_id}/flags")
    def flags(run_id: str, column: str = "", offset: int = Query(0, ge=0),
              limit: int = 100) -> dict[str, Any]:
        def build(r: list[str]) -> dict[str, Any]:
            r = r + [""] * (4 - len(r))
            return {"row_key": r[0], "column": r[1], "value": r[2], "message": r[3]}
        return page(run_id, "flags.csv", column, offset, limit, "flags_written", build)

    @app.get("/api/runs/{run_id}/files/{name}")
    def download(run_id: str, name: str) -> FileResponse:
        if not RUN_ID_RE.match(run_id):
            raise HTTPException(404, "Unknown run")
        run_dir = output_root() / run_id
        for fname in list_run_files(run_dir):  # match against the listing; no path joins
            if fname == name:
                return FileResponse(run_dir / fname, filename=fname)
        raise HTTPException(404, "Unknown file")

    @app.get("/api/{rest:path}")
    @app.post("/api/{rest:path}")
    def api_404(rest: str):
        raise HTTPException(404, "Not found")

    # ------------------------------------------------------------------ static UI
    dist = get_settings().web_dist

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str):
        if dist is None or not (dist / "index.html").is_file():
            return HTMLResponse(
                "<!doctype html><title>Scrubber</title><h1>Scrubber API is running</h1>"
                "<p>The web UI is not built. Run <code>npm run build</code> in "
                "<code>web/</code> and restart <code>scrubber serve</code>.</p>")
        root = dist.resolve()
        cand = (root / full_path).resolve()
        if full_path and cand.is_file() and cand.is_relative_to(root):
            return FileResponse(cand)
        return FileResponse(root / "index.html")

    return app
