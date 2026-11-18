# Phase 1 — Backend core (plan model, ops, profiler, LLM, planner, cleaner, CSV)

**Source of truth:** `docs/SPEC.md`. Where this plan and the spec disagree, the spec wins;
note the disagreement in your final report. Read the whole spec before starting
(§3, §4, §5, §6, §7 intro + §7.1, §8, §12, §13 are directly in scope).

## Ground rules
- Work only under `backend/`. Do not touch `web/`, `docs/`, the old `src/`, the old
  root `tests/`, or any other root file. Do not run git commands that change state
  (no commit, add, checkout, stash, reset).
- No network calls. Never call OpenAI. All LLM behaviour is tested with `FakeLLM`.
- Python env: `/home/biswa/workspace/csv_rule_based_cleaner/.venv` (Python 3.12).
  Install with `uv pip install --python /home/biswa/workspace/csv_rule_based_cleaner/.venv -e "/home/biswa/workspace/csv_rule_based_cleaner/backend[dev]"`.
  Run tests from `backend/`: `../.venv/bin/python -m pytest -q`.
- Draft code already exists in `backend/src/scrubber/` (plan.py, ops.py, profiler.py,
  llm.py, prompts.py, config.py, connectors/*). It predates the spec review and is wrong
  in several places (async LLM, `only_if_mismatch`, `translate` strings, digit-collapsing
  shapes, OFFSET pagination, flatten/unflatten writer, …). Treat it as a rough draft:
  rewrite freely to match the spec. Leave `connectors/sql_source.py` and
  `connectors/mongo_source.py` untouched — Phase 2 rewrites them. Make sure nothing in
  Phase 1 imports them at module import time (`open_source` imports lazily; keep it so).
- Everything synchronous (no asyncio anywhere). Type hints everywhere. Keep modules
  small and readable; no speculative abstractions.

## Step 1 — Packaging & config
1. `backend/pyproject.toml`: dependencies exactly per spec §3 (add `langchain`,
   `langchain-openai`, `python-dotenv`; remove `openai`; dev: `pytest`, `mongomock`,
   `httpx`; remove `pytest-asyncio` and `asyncio_mode`). Keep the `scrubber` script entry
   (`scrubber.cli:app`) even though `cli.py` arrives in Phase 2.
2. `config.py`: `load_dotenv()` at import, then `Settings(BaseSettings)` with every var in
   §12 (types: ints, float timeout, `llm_model_kwargs: dict` parsed from JSON,
   `planner_model: str | None`, `web_dist: Path | None` defaulting to
   `<repo>/web/dist` computed from `Path(__file__).resolve().parents[3] / "web" / "dist"`).
   `get_settings()` cached with `functools.lru_cache`; tests reset it with
   `get_settings.cache_clear()`. Add a `tests/conftest.py` fixture that does that and sets
   `OUTPUT_DIR`/`UPLOAD_DIR` to `tmp_path`.

## Step 2 — `plan.py`
Pydantic models exactly per §4/§4.2: base `_Op` with `enabled`, `note`,
`model_config = ConfigDict(extra="forbid")`; ops `trim`, `collapse_whitespace`, `case`,
`regex_replace`, `reformat`, `translate` (`mapping: dict[str, str]`, `ignore_case=False`),
`map_values`, `map_from_column` (`mode: Literal["overwrite","fill_empty"] = "overwrite"`),
`null_if`, `parse_date`, `validate`, `llm_fix`; discriminated union `Op` on `op`.
`ColumnPlan`, `Plan` (`version: Literal[1] = 1`, `rules`, `columns`, `notes`), and
`PlannerOutput` (`columns`, `notes` — what the LLM returns). All with `extra="forbid"`.
Also export `OP_NAMES`.

## Step 3 — `connectors/base.py` + `connectors/csv_source.py` + `connectors/__init__.py`
Per §7 and §7.1 exactly:
- `ColumnInfo`, `Connector` (abstract: `description`, `null_equals_empty`,
  `column_info()`, `count()`, `sample(n)`, `iter_batches(size)`,
  `writer(*, run_dir, in_place, overwrite)`, `close()`; context-manager support),
  `Writer` (context manager, `target`, `write(batch)`, `close()`), `SourceError`,
  `OutputExistsError(SourceError)` (used when the target exists and not `overwrite`;
  API maps it to 409), source spec models `CSVSource`, `SQLSource` (`url, table,
  db_schema` aliased to `schema` in JSON? — use field name `db_schema` with
  `Field(alias="schema")` and `populate_by_name=True`, because `schema` shadows a pydantic
  attribute), `MongoSource`, and `SourceSpec` as a discriminated union on `kind`.
- CSV connector: header validation errors, padding short rows, `__extra__` for long rows
  (row-level flag is raised by the cleaner when it sees `__extra__`), seeded reservoir
  `sample` that also caches `count`, writer re-emitting extra fields, `in_place` →
  `SourceError`. `RowKey` for CSV is the int row index.
- `open_source(spec, mongo_client=None)` dispatching lazily.

## Step 4 — `ops.py`
- Cell helpers: `is_empty`, `stringify` (§4.1 read rule), regex compile cache raising
  `PlanError`-free plain `re.error` wrapped as message strings for `check_plan`.
- One function per op implementing §4.2 exactly (title-case regex
  `[^\W\d_]+(?:'[^\W\d_]+)*`; `parse_date` double parse; `map_from_column` both modes with
  the exact conflict flag text; `null_if` matching; `translate` with `ignore_case`).
- `llm_fix` per §4.3: trigger rules, `LLMKey` frozen dataclass
  `(column, op_index, value, context: tuple[tuple[str,str],...])`, acceptance algorithm
  (re-run preceding enabled `validate`/`parse_date` on the candidate; `if_not_match`
  pattern; `max_length`), exact flag messages.
- Resolver contract: `Resolver = Callable[[LLMKey], Answer | None]` where
  `Answer(value: str | None, fixed: bool, reason: str = "", failure: str | None = None)`.
  `failure` set → the cell is flagged with exactly that message and not changed (used for
  `"LLM budget exceeded"`, `"LLM error: …"`, `"LLM skipped"`). Resolver returns `None` in
  the write pass → flag `"LLM fix not attempted (source changed during run)"`. When the
  resolver itself is `None` (dry-run/scan) the key is recorded and the value passes
  through unchanged with no flag.
- `apply_row(plan, row, resolver=None, *, columns: dict[str, ColumnInfo] | None = None,
  null_equals_empty=False) -> RowResult` with: `values` (final dict, includes
  `__extra__` untouched), `changed: dict[col, (before, after, sources: list[str])]`,
  `flags: list[CellFlag(column, op_index, value, message)]` (op_index `None` for
  row-level / max_length flags), `op_stats: dict[(col, op_index), [changed, flagged]]`,
  `llm_keys: list[LLMKey]`. Implements the `max_length` revert-and-flag and
  `null_equals_empty` diffing (§6 write pass) and the `__extra__` row flag
  (`column=""`, `"Row has N extra field(s)"`).
- `check_plan(plan, columns: list[ColumnInfo]) -> tuple[list[str], list[str]]` — rules
  R1 is pydantic (callers validate first); implement R2–R9 (R9 in-place key check is a
  separate helper `check_in_place(plan, key_column)` used by the cleaner) and the
  `always` warning. Error strings must be specific (column, op index, op name, problem).
  R5 replacement check via `compiled.sub(replacement, "")` and the `$<digit>` rejection.

## Step 5 — `profiler.py`
§5.1 exactly: `ShapeStat`, `ColumnProfile` (with `cleanable`), `Profile`; `shape_of`
(digits preserved), stats over stringified non-empty values, `rare_examples`, `all_values`
thresholds (≤60 distinct and ≤2000 chars total), truncation to 120 chars + `…`.
`profile_rows(rows, columns: list[ColumnInfo], source, total) -> Profile`.

## Step 6 — `llm.py` + `prompts.py`
- `LLMUsage` (calls, failed_calls, input_tokens, output_tokens; lock-guarded `add_call`),
  `LLMError`, `LLM` protocol per §8, `extract_json`, `LangChainLLM(model)` using
  `init_chat_model(model, timeout=…, max_retries=…, **llm_model_kwargs)`; when the
  provider prefix is `openai` bind `response_format={"type": "json_object"}` via
  `.bind(response_format=…)`; never pass temperature or max tokens. Parse → validate →
  one repair round → `LLMError`. Counts failed calls. Construction errors (missing
  `OPENAI_API_KEY`, unknown provider) → `LLMError` with a clear message, raised at
  construction. `make_llm(*, planner: bool = False)` picks `PLANNER_MODEL` when
  `planner` and set, else `LLM_MODEL`.
- `FakeLLM(responses)` per §8 (list consumed in order, or callable), records
  `calls: list[(messages, schema_name)]`, has `model_name="fake"` and `usage`.
- `prompts.py`: rewrite to match the final semantics in §4.2/§4.3/§5.2 (principles list
  verbatim in spirit, exact op catalog incl. defaults, `map_from_column` default
  `overwrite`, ordering rules R6–R8, the word "JSON", data-not-instructions). Fixer prompt
  includes `must_match` patterns and says it must not blank values. Keep prompts free of
  domain facts.

## Step 7 — `planner.py`
- `profile(connector, columns=None) -> Profile` (uses `SAMPLE_SIZE`).
- `PlanError(errors: list[str])`.
- `plan(connector, rules, llm, columns=None) -> PlanResult(plan, preview, usage)` per
  §5.2 including the profile-size degradation and the repair conversation (append raw
  answer + error list; at most `PLANNER_MAX_REPAIRS` rounds). Schema errors from
  `complete_json`'s own repair exhausting → treat as a planner error round too? Keep it
  simple: `LLMError` from `complete_json` propagates (API 502); `check_plan` errors drive
  the planner repair loop.
- `dry_run(plan, rows, total_rows, columns: list[ColumnInfo], null_equals_empty) ->
  Preview` per §5.3, using `apply_row` with no resolver; examples/flag_examples distinct
  and capped at 5; warnings = `check_plan` warnings + "no effect on sample" per op.
- `preview(connector, plan) -> Preview` convenience (validates with `check_plan` first,
  raising `PlanError`).

## Step 8 — `cleaner.py`
`clean(connector, plan, llm, *, in_place=False, overwrite=False, no_llm=False,
max_llm_cells=None, run_dir: Path, progress=None, cancel=None) -> RunReport` exactly per
§6: preconditions; scan (skipped when no enabled llm_fix or `no_llm`); resolve with
`ThreadPoolExecutor(LLM_CONCURRENCY)`, chunks of `LLM_CELLS_PER_CALL`, `must_match`,
id handling, 3-consecutive-failures stop, cancel checks; write pass streaming
`changes.csv`/`flags.csv` (columns per §6; `sources` joined with `;` in CSV), `plan.json`,
`report.json` (all fields listed in §6; `status` done/failed/cancelled; exceptions caught
and recorded, then re-raised? — No: `clean` returns the report with `status="failed"`;
the CLI/API decide what to do). `llm` may be `None` when the plan has no enabled
`llm_fix` or `no_llm`. `new_run_id()` → `YYYYMMDD-HHMMSS-<4 hex>`. `RunReport` is a
pydantic model mirroring report.json. Progress callback per §6.

## Step 9 — Tests (`backend/tests/`)
Everything listed in spec §13 for: ops, check_plan, llm_fix, profiler, planner, cleaner,
CSV connector, and the golden test (`examples/leads.csv` does not exist yet — copy
`test_leads_with_issues.csv` from the repo root into `backend/tests/data/leads.csv`
and write a hand-made plan JSON next to it, plus the expected output CSV generated once
and checked by eye for correctness). Also a test that `LangChainLLM` binds JSON mode and
never passes temperature: monkeypatch `init_chat_model` to return a stub chat model.
Aim for focused, fast tests (< 10 s total).

## Done when
- `../.venv/bin/python -m pytest -q` passes from `backend/`.
- `python -c "import scrubber.planner, scrubber.cleaner"` works.
- Final report lists: files created/changed, any spec ambiguities and how you resolved
  them, anything left undone, and the test count.
