"""In-process source registry and run registry for the API (spec §10)."""

from __future__ import annotations

import json
import re
import threading
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from pydantic import BaseModel

from .cleaner import RunReport, clean, new_run_id
from .config import get_settings
from .connectors import ColumnInfo, Connector, CSVSource, MongoSource, SQLSource, open_source
from .llm import LLM
from .plan import Plan

RUN_ID_RE = re.compile(r"^\d{8}-\d{6}-[0-9a-f]{4}$")

SourceSpec = CSVSource | SQLSource | MongoSource


# ----------------------------------------------------------------------- sources


@dataclass
class SourceEntry:
    source_id: str
    spec: SourceSpec
    description: str
    can_in_place: bool
    column_info: list[ColumnInfo] | None = None  # Mongo: columns found at registration, reused on every open


class SourceRegistry:
    """source_id -> spec. Specs (which may hold credentials) never leave this object."""

    def __init__(self, mongo_client: Any = None):
        self._items: dict[str, SourceEntry] = {}
        self._lock = threading.Lock()
        self._mongo_client = mongo_client

    def open(self, spec: SourceSpec, columns: list[ColumnInfo] | None = None) -> Connector:
        return open_source(spec, mongo_client=self._mongo_client, columns=columns)

    def open_entry(self, entry: SourceEntry) -> Connector:
        return self.open(entry.spec, entry.column_info)

    def add(self, spec: SourceSpec, description: str,
            column_info: list[ColumnInfo] | None = None) -> SourceEntry:
        entry = SourceEntry(uuid.uuid4().hex, spec, description, not isinstance(spec, CSVSource),
                            column_info if isinstance(spec, MongoSource) else None)
        with self._lock:
            self._items[entry.source_id] = entry
        return entry

    def get(self, source_id: str) -> SourceEntry | None:
        with self._lock:
            return self._items.get(source_id)



# ----------------------------------------------------------------------- runs


class RunStatus(BaseModel):
    run_id: str
    status: str  # queued | running | done | failed | cancelled
    phase: str | None = None
    done: int = 0
    total: int | None = None
    error: str | None = None
    report: dict[str, Any] | None = None
    files: list[str] = []


def output_root() -> Path:
    return Path(get_settings().output_dir)


def list_run_files(run_dir: Path) -> list[str]:
    try:
        return sorted(p.name for p in run_dir.iterdir() if p.is_file())
    except OSError:
        return []


def read_report(run_dir: Path) -> dict[str, Any] | None:
    try:
        return json.loads((run_dir / "report.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


@dataclass
class RunHandle:
    run_id: str
    run_dir: Path
    cancel: threading.Event = field(default_factory=threading.Event)
    status: str = "queued"
    phase: str | None = None
    done: int = 0
    total: int | None = None
    error: str | None = None
    report: dict[str, Any] | None = None
    future: Future | None = None


class RunRegistry:
    def __init__(self) -> None:
        self._runs: dict[str, RunHandle] = {}
        self._lock = threading.Lock()
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="scrubber-run")

    def new_handle(self) -> RunHandle:
        root = output_root()
        with self._lock:
            while True:
                run_id = new_run_id()
                if run_id not in self._runs and not (root / run_id).exists():
                    break
            h = RunHandle(run_id, root / run_id)
            self._runs[run_id] = h
        return h

    def submit(self, handle: RunHandle, job: Callable[[RunHandle], None]) -> None:
        handle.future = self._pool.submit(job, handle)

    def update(self, handle: RunHandle, **kw: Any) -> None:
        with self._lock:
            for k, v in kw.items():
                setattr(handle, k, v)

    def get(self, run_id: str) -> RunHandle | None:
        with self._lock:
            return self._runs.get(run_id)

    def status(self, run_id: str) -> RunStatus | None:
        if not RUN_ID_RE.match(run_id):
            return None
        h = self.get(run_id)
        if h is not None:
            with self._lock:
                finished = h.status in ("done", "failed", "cancelled")
                snap = RunStatus(run_id=run_id, status=h.status, phase=h.phase, done=h.done,
                                 total=h.total, error=h.error, report=h.report)
            snap.files = list_run_files(h.run_dir) if finished else []
            return snap
        # unknown to this process: fall back to a finished run on disk
        run_dir = output_root() / run_id
        rep = read_report(run_dir)
        if rep is None:
            return None
        return RunStatus(run_id=run_id, status=rep.get("status", "done"), error=rep.get("error"),
                         report=rep, files=list_run_files(run_dir),
                         done=rep.get("rows_written", 0), total=rep.get("rows"))

    def cancel(self, run_id: str) -> bool:
        h = self.get(run_id)
        if h is None:
            return False
        h.cancel.set()
        return True

    def shutdown(self) -> None:
        with self._lock:
            for h in self._runs.values():
                h.cancel.set()
        self._pool.shutdown(wait=False, cancel_futures=True)


def _write_failed_report(handle: RunHandle, plan: Plan, error: str, status: str = "failed") -> RunReport:
    rep = RunReport(run_id=handle.run_id, status=status, error=error)
    handle.run_dir.mkdir(parents=True, exist_ok=True)
    (handle.run_dir / "plan.json").write_text(plan.model_dump_json(indent=2), encoding="utf-8")
    (handle.run_dir / "report.json").write_text(rep.model_dump_json(indent=2), encoding="utf-8")
    return rep


def execute_run(registry: RunRegistry, handle: RunHandle, open_connector: Callable[[], Connector],
                plan: Plan, llm: LLM | None, *, in_place: bool, overwrite: bool, no_llm: bool,
                max_llm_cells: int | None) -> None:
    """Worker-thread body: runs `clean` and mirrors progress/outcome into the handle."""
    if handle.cancel.is_set():
        rep = _write_failed_report(handle, plan, "", "cancelled")
        registry.update(handle, status="cancelled", report=rep.model_dump())
        return
    registry.update(handle, status="running")

    def progress(phase: str, done: int, total: int | None) -> None:
        registry.update(handle, phase=phase, done=done, total=total)

    try:
        with open_connector() as conn:
            rep = clean(conn, plan, llm, in_place=in_place, overwrite=overwrite, no_llm=no_llm,
                        max_llm_cells=max_llm_cells, run_dir=handle.run_dir, progress=progress,
                        cancel=handle.cancel)
    except Exception as e:  # precondition failures that surfaced inside the worker
        rep = _write_failed_report(handle, plan, str(e) or type(e).__name__)
    registry.update(handle, status=rep.status, phase=None, error=rep.error,
                    report=rep.model_dump())
