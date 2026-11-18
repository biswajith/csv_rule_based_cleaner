# Phase 2 — SQL & MongoDB connectors, CLI, HTTP API

**Prerequisite:** Phase 1 is merged in the working tree (`backend/src/scrubber/` has
`plan.py`, `ops.py`, `profiler.py`, `llm.py`, `prompts.py`, `planner.py`, `cleaner.py`,
`connectors/base.py`, `connectors/csv_source.py`, and passing tests). Read those modules
first — build on their actual interfaces (`Connector`, `Writer`, `ColumnInfo`,
`SourceSpec`, `OutputExistsError`, `open_source`, `planner.profile/plan/preview`,
`cleaner.clean`, `make_llm`, `FakeLLM`, `get_settings`). Do not change Phase 1 behaviour;
if an interface truly needs a change, make the smallest change, keep its tests green, and
call it out in your report.

**Source of truth:** `docs/SPEC.md` — §7.2, §7.3, §9, §10, §10.1, §12, §13 (connectors,
api), §14 are directly in scope. Where this plan and the spec disagree, the spec wins.

## Ground rules
- Work only under `backend/` plus these root files: `.env.example` (new), `.gitignore`
  (append entries per §14). Do not touch `web/` or `docs/`. No git state changes.
- No network calls; no real OpenAI calls (FakeLLM via dependency injection). MongoDB
  tests use `mongomock`; SQL tests use SQLite files under `tmp_path`.
- Python env and test command as in Phase 1:
  `cd backend && ../.venv/bin/python -m pytest -q`.

## Step 1 — `connectors/sql_source.py` (rewrite the draft) — spec §7.2
- Reflect the table (SQLAlchemy 2 Core), `description` with hidden password.
- Key resolution + `key_column` uniqueness/non-null check at connect.
- `column_info()`: `cleanable` = `isinstance(col.type, sa.String) and not
  isinstance(col.type, sa.Enum)`; `max_length` = `col.type.length`; `nullable`.
- `iter_batches`: keyset pagination, one short-lived connection per batch, never OFFSET.
- `sample(n)`: first n by key.
- Writers: new table `<table>_cleaned` (same columns/types/PK only; exists →
  `OutputExistsError` unless `overwrite`, then drop+create), one transaction per batch;
  in-place `UPDATE … WHERE key = :key` for changed columns only, one transaction per batch.
- `count()`, `close()` (dispose engine).
- Check what the cleaner needs for R9 / in-place key check (`check_in_place`) and expose
  the key column (e.g. `key_column` attribute) for it.

## Step 2 — `connectors/mongo_source.py` (rewrite the draft) — spec §7.3
- Constructor takes an optional injected client (mongomock in tests).
- Flatten rules (nested docs → dotted paths; arrays and empty docs are leaves), columns =
  union over the `$sample` sample (excluding `_id`), absent → `None`, `cleanable` rule.
- `sample(n)` via aggregate `$match` + `$sample` (mongomock supports `$sample`; if it
  doesn't in the installed version, fall back to `find(filter).limit(n)` only when the
  client is mongomock — keep that branch tiny and commented).
- `iter_batches`: sort `_id`, keyset `_id > last` + limit.
- New-collection writer: re-read originals by `_id` (`$in`, one query per batch), set only
  changed dotted paths on a deep copy (helper `set_path(doc, "a.b", v)`), insert; exists →
  `OutputExistsError` unless `overwrite`.
- In-place writer: `bulk_write([UpdateOne({_id}, {$set: {...changed paths}})])`, null as
  null, never `$unset`.
- Row keys: `_id` values; when stringified for CSV/API output use `str(_id)`.

## Step 3 — `cli.py` (Typer + rich) — spec §9
- Commands `profile`, `plan`, `apply`, `run`, `serve` with the options listed in §9.
- SOURCE detection (`mongodb://`/`mongodb+srv://` → Mongo; contains `://` → SQL; else
  CSV path; `env:NAME` indirection) → `SourceSpec`; missing required options → exit 2
  with a clear message.
- Rules file parsing (UTF-8, skip blanks and `#` lines).
- `plan`: profile → plan (planner LLM via `make_llm(planner=True)`) → dry run; rich
  tables for plan (column, op, summary), preview (per op changed/flagged), warnings,
  notes, LLM estimate wording per §5.3, usage; write `-o` (default `plan.json`).
- `apply`: load + validate plan JSON (pydantic + `check_plan`), in-place typed
  confirmation unless `--yes`, run `clean` with a rich progress bar driven by the
  progress callback, Ctrl-C → set cancel event → exit 130. Print the summary (§9).
  `make_llm()` only constructed when the plan has an enabled `llm_fix` and not
  `--no-llm`.
- `run`: plan → show → `Proceed? [y/N]` unless `--yes` → apply.
- `serve`: uvicorn on `--host/--port`; refuse non-loopback host unless `--allow-remote`
  (print the no-auth warning); pass port/allow-remote into the app factory for the Host
  check.
- Exit codes exactly per §9. Never print URLs/URIs — only `description`.

## Step 4 — `runs.py` + `api.py` — spec §10, §10.1
- `create_app(*, port: int, allow_remote: bool = False, dev: bool | None = None,
  llm_factory: Callable[[bool], LLM] | None = None) -> FastAPI` — `llm_factory(planner)`
  defaults to `make_llm(planner=…)`; tests inject FakeLLM. `dev` defaults from
  `SCRUBBER_DEV=1`.
- `runs.py`: `SourceRegistry` (source_id → (spec, connector-free metadata); open a fresh
  connector per request/run, close it after; thread-safe dict + lock), `RunRegistry`
  (single-worker `ThreadPoolExecutor`; RunStatus updated from the progress callback;
  cancel events; fallback to `<OUTPUT_DIR>/<run_id>/report.json` for unknown ids).
- Endpoints exactly per the §10 table with the shapes listed there. Plain `def`
  endpoints. Uploads streamed to `UPLOAD_DIR/<uuid>.csv` with the 413 limit; display
  name sanitised. `changes`/`flags` paging by scanning the CSVs (`limit` ≤ 1000,
  `column` filter, `total` per spec). `files/{name}` only for names in the run's file
  list; `run_id` regex check → 404.
- Error mapping: `SourceError` → 400, `OutputExistsError` → 409, `PlanError` → 422 with
  `errors`, `LLMError` → 502, missing API key → 400; body `{detail, errors?}` everywhere
  (custom exception handlers incl. FastAPI's own 422 request-validation errors).
- Security middleware per §10.1 (Host + Origin checks on `/api/*`; dev origin; no CORS).
- Static: serve `WEB_DIST` (index.html + assets) at `/` with SPA fallback to
  index.html for non-`/api` paths; if missing, `/` returns a plain HTML page saying to
  run `npm run build` in `web/`.

## Step 5 — Root files
- `.env.example` documenting every §12 variable with defaults and comments
  (`PLANNER_MODEL` commented out with a note that the owner's choice is pending its exact
  ID).
- `.gitignore`: append `output/`, `data/`, `web/node_modules/`, `web/dist/`, `.venv/`
  if not already ignored (check first; don't duplicate).

## Step 6 — Tests
Per spec §13 `connectors` (SQLite + mongomock lists) and `api` lists, plus CLI tests
with Typer's `CliRunner` (SOURCE detection, exit codes 0/1/2, `apply --no-llm` on a
CSV end-to-end, `plan` with FakeLLM injected by monkeypatching `scrubber.cli.make_llm`).

## Done when
- All backend tests pass (Phase 1 + Phase 2).
- `../.venv/bin/scrubber --help` works after `uv pip install -e "backend[dev]"`.
- Manual smoke: `scrubber apply <repo>/backend/tests/data/leads.csv --plan <the golden
  plan> --no-llm --out <tmp>` produces the cleaned CSV, changes.csv, flags.csv,
  report.json.
- Final report: files, test count/result, interface changes to Phase 1 code (if any),
  spec ambiguities and resolutions, anything not done.
