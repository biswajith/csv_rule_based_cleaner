# Scrubber — Planner/Cleaner Rewrite Spec

Status: spec reviewed (see §15), implementation pending · Branch: `rewrite/planner-cleaner` ·
Replaces: `src/csv_cleaner` (v0.1)

The draft code under `backend/src/scrubber/` predates this revision. Where the two differ,
**this document wins** and the code must be changed.

## 1. Why rewrite

The v0.1 `csv_cleaner` sends every chunk of 50 rows to the LLM, asks it to rewrite every
row *and* self-report a `change_log`, then spends ~4k lines of scaffolding (consistency
checks, validation reporter, change tracker, retries, memory optimizer, performance
monitor, hybrid/vector processors) catching the LLM dropping rows or logging changes it
didn't make. Problems:

| Problem | Effect |
|---|---|
| LLM processes every row | Cost and latency grow linearly with row count |
| LLM does regex-shaped work (phone formats, casing, country codes) | Paying LLM rates for deterministic transforms; non-deterministic output |
| Change log is self-reported by the LLM | Log and data disagree; large prompt sections exist only to beg for consistency |
| Hardcoded domain hacks in prompts (`Miami→FL`, keypad table) | Doesn't generalize; prompts tuned to one test file |
| Expanded rules are free-form prose | Not reviewable, not reusable, not testable |
| CSV-only, pandas-centric | No path to SQL or MongoDB |
| No real UI | CLI script with `input()` prompt |

## 2. Goals / non-goals

**Goals**
1. **Plan first, then clean.** One planning phase (LLM) produces a structured, reviewable
   plan; one cleaning phase (code) executes it over all records.
2. **The LLM delegates to code.** The planner's job is to say "fix this column with this
   regex / lookup table / case rule". The LLM only touches individual cells when a
   judgement call is genuinely required (`llm_fix`), and those cells are collected across
   the whole dataset, de-duplicated by value, and resolved in batched calls.
3. **Truthful change log.** Changes are computed by diffing before/after values, never
   reported by the LLM.
4. **Sources:** CSV, SQL (any SQLAlchemy URL), MongoDB.
5. **Provider-swappable LLM** via LangChain (`init_chat_model`). OpenAI is the only
   provider installed/supported for now.
6. **UX:** a CLI for scripting and a local web UI (React + React Spectrum) for the
   connect → profile → plan → review → run → inspect loop.
7. **Plans are artifacts:** saved as JSON, editable by hand or in the UI, re-runnable on
   new data with zero LLM cost (unless they contain `llm_fix` ops, or `--no-llm` is used).

**Non-goals (this iteration)** — do not build these:
- Deleting/deduplicating records. Records are always preserved; problems are *flagged*.
  There is no duplicate-detection op (it needs cross-row state); such rules go to `notes`.
- Multi-table / relational cleaning (joins across tables).
- Auth/multi-user for the web UI (local tool bound to loopback; see §10.1).
- Streaming/real-time sources.
- Providers other than OpenAI (the seam exists; adapters are a config change + package).
- Changing a column's data type. Ops only rewrite **string** cells (§4.2).
- Cleaning non-string SQL columns, Mongo arrays, CSV in-place, SQL tables without a
  single-column unique key (§7).
- Caching LLM resolutions between runs, dollar-cost estimates (token counts only),
  regex timeouts, SSE/websockets (the UI polls), form-based op editors (JSON edit only),
  a run-history page.

## 3. Architecture

```
            ┌──────────────┐  sample   ┌───────────┐  profile+rules  ┌──────────┐
 Source ───▶│  Connector   │──────────▶│ Profiler  │────────────────▶│ Planner  │──LLM (1–3 calls)
 (csv/sql/  │ sample/iter/ │           └───────────┘                 │ +validate│
  mongo)    │ write        │                                         │ +dry-run │
            └──────┬───────┘                                         └────┬─────┘
                   │                                                      │ Plan (JSON)
                   │                      human review / edit (UI or file)│
                   ▼                                                      ▼
            ┌─────────────────────────────── Cleaner ─────────────────────────────┐
            │ scan    : apply ops to every record, write nothing, collect unique  │
            │           llm_fix requests (skipped when the plan has no llm_fix)   │
            │ resolve : batched LLM calls for the unique requests only            │
            │ write   : re-apply ops, substitute LLM answers, diff → changes,     │
            │           flags, write output                                       │
            └─────────────────────────────────────────────────────────────────────┘
                   │
                   ▼
   cleaned output (new file / table / collection, or in-place) + report.json
   + changes.csv + flags.csv
```

Everything in the backend is **synchronous** Python. There is no `asyncio` in our code:
LLM concurrency uses a `ThreadPoolExecutor`; FastAPI endpoints are plain `def` (FastAPI
runs them in its threadpool); runs execute in a worker thread (§10).

Package layout (`backend/src/scrubber/`):

| Module | Responsibility |
|---|---|
| `plan.py` | Pydantic models: `Plan`, `ColumnPlan`, the op union |
| `ops.py` | Pure functions: apply a column's ops to one cell; `check_plan`; regex compile cache |
| `profiler.py` | Column statistics from a sample |
| `llm.py` | LangChain wrapper: `complete_json` with pydantic validation + one repair, usage accounting; `FakeLLM` |
| `prompts.py` | Planner and cell-fixer prompts (no hardcoded domain facts) |
| `planner.py` | profile + rules → validated `Plan`; `dry_run(plan, sample)` → `Preview` |
| `cleaner.py` | scan / resolve / write executor, diff-based change log, run report |
| `connectors/` | `base.py`, `csv_source.py`, `sql_source.py`, `mongo_source.py`, `__init__.py` (`open_source`) |
| `config.py` | `pydantic-settings` settings (env / `.env`) |
| `cli.py` | Typer CLI: `profile`, `plan`, `apply`, `run`, `serve` |
| `api.py` | FastAPI app for the web UI; serves `web/dist` |
| `runs.py` | In-process source registry + run registry for the API |

`web/` (repo root) — Vite + React 18 + TypeScript + `@adobe/react-spectrum` (v3).

Python ≥ 3.11. Dependencies: `langchain`, `langchain-openai`, `pydantic`,
`pydantic-settings`, `python-dotenv`, `typer`, `rich`, `fastapi`, `uvicorn`,
`python-multipart`, `sqlalchemy>=2`, `pymongo`, `python-dateutil`. Dev: `pytest`,
`mongomock`, `httpx`. (The draft `pyproject.toml` lists `openai` and `pytest-asyncio`
and omits the LangChain packages and `python-dotenv` — fix it.)

## 4. The Plan (contract between planner and cleaner)

```json
{
  "version": 1,
  "rules": ["Phone numbers should be (XXX) XXX-XXXX", "..."],
  "columns": [
    {
      "column": "phone",
      "rationale": "Mixed separators, some keypad letters; normalize to (XXX) XXX-XXXX",
      "ops": [
        {"op": "translate", "mapping": {"A": "2", "B": "2", "C": "2", "D": "3"}, "ignore_case": true},
        {"op": "regex_replace", "pattern": "[^0-9]", "replacement": ""},
        {"op": "reformat", "pattern": "1?(\\d{3})(\\d{3})(\\d{4})", "template": "(\\1) \\2-\\3"},
        {"op": "validate", "pattern": "\\(\\d{3}\\) \\d{3}-\\d{4}", "message": "Not a 10-digit US number"}
      ]
    },
    {
      "column": "state",
      "ops": [
        {"op": "trim"},
        {"op": "map_values", "mapping": {"California": "CA", "New York": "NY"}},
        {"op": "map_from_column", "source_column": "city", "mapping": {"Miami": "FL", "Austin": "TX"}, "mode": "overwrite"},
        {"op": "validate", "pattern": "[A-Z]{2}", "message": "Not a 2-letter state code"}
      ]
    },
    {
      "column": "email",
      "ops": [
        {"op": "trim"}, {"op": "case", "mode": "lower"},
        {"op": "regex_replace", "pattern": "@{2,}", "replacement": "@"},
        {"op": "validate", "pattern": "[^@\\s]+@[^@\\s]+\\.[a-z]{2,}", "message": "Invalid email"},
        {"op": "llm_fix", "when": "if_flagged", "instruction": "Repair obviously mistyped email addresses (e.g. misspelled well-known domains)", "context_columns": ["first_name", "last_name"]}
      ]
    }
  ],
  "notes": ["Rule 'remove duplicate records' not applied: records are always preserved"]
}
```

`Plan` fields: `version: int = 1` (reject anything else), `rules: list[str]`,
`columns: list[ColumnPlan]`, `notes: list[str]`. `ColumnPlan`: `column: str`,
`rationale: str = ""`, `ops: list[Op]`. A plan does **not** contain the source; the same
plan can be applied to any source that has the referenced columns.

All plan models use `model_config = ConfigDict(extra="forbid")` so a misspelled parameter
(`ignorecase`) is a validation error instead of being silently ignored.

### 4.1 Definitions used by every op

- **Cell value** is whatever the connector yields: `str`, `None`, or (SQL/Mongo only)
  another Python type.
- **Empty**: `None`, or a `str` that is empty or whitespace-only.
- **String-only rule:** every op, including `validate` and `llm_fix`, acts only on `str`
  values. A non-empty non-`str` value (int, float, bool, date, Decimal, bytes, list,
  ObjectId…) passes through every op untouched and is never flagged, changed or sent to
  the LLM. `None` also passes through untouched, with exactly two exceptions:
  `map_from_column` may fill it, and `validate` with `allow_empty=false` flags it.
- Whenever another column is *read* (`map_from_column.source_column`,
  `llm_fix.context_columns`) its value is stringified: `None` → `""`, `str` as is,
  anything else → `str(v)`; then `.strip()`.
- **Regex:** Python `re` syntax, compiled once and cached. "Full match" means
  `re.fullmatch` on the value exactly as it is at that point (no implicit strip).
  Replacement strings use Python syntax (`\1`, `\g<name>`), not `$1`.
- **Order:** columns are processed in plan order; within a column ops run top to bottom,
  each seeing the previous op's output. The row is a dict that is updated as each column
  finishes, so a read of another column sees that column's *cleaned* value if it is
  planned earlier, else its raw value. Disabled ops (`enabled=false`) are skipped but
  keep their index (op indices always refer to positions in the full `ops` list).
- **Flag:** a message attached to a cell (`row_key, column, op_index, message`). Flags
  never change data. A flag records that the value failed *at that op*; later
  deterministic ops do not clear it (so put `validate` after the transforms).

### 4.2 Op catalog

Every op has `enabled: bool = true` and `note: str = ""`. Defaults shown are normative.

| Op | Params (default) | Exact behaviour |
|---|---|---|
| `trim` | – | `value.strip()` |
| `collapse_whitespace` | – | `re.sub(r"\s+", " ", value)` (does not trim) |
| `case` | `mode: lower\|upper\|title` (required) | `lower()` / `upper()` / title: for every maximal run matching `[^\W\d_]+(?:'[^\W\d_]+)*`, uppercase first char, lowercase the rest (`o'brien`→`O'brien`, `mcdonald's`→`Mcdonald's`, `IBM`→`Ibm` — lossy; the planner prompt warns about this) |
| `regex_replace` | `pattern` (req), `replacement` (`""`), `ignore_case` (`false`) | `re.sub(pattern, replacement, value)`, all occurrences |
| `reformat` | `pattern` (req), `template` (req), `ignore_case` (`false`) | if value **fully** matches: `match.expand(template)`; else unchanged (no flag) |
| `translate` | `mapping: {char: str}` (req), `ignore_case` (`false`) | per-character replacement. Every key is exactly one character; values may be any string incl. `""`. With `ignore_case`, a character with no exact key is looked up by its `.upper()` then `.lower()` |
| `map_values` | `mapping: {str: str}` (req), `ignore_case` (`true`) | look up `value.strip()` (casefolded on both sides when `ignore_case`); hit → mapped value, miss → unchanged. Exact-case key wins over a folded match; among folded collisions the first key in mapping order wins |
| `map_from_column` | `source_column` (req), `mapping: {str: str}` (req), `ignore_case` (`true`), `mode: overwrite\|fill_empty` (`overwrite`) | see below |
| `null_if` | `values: list[str]` (`[]`), `pattern: str\|null` (`null`) | value → `None` if `value.strip()` casefold-equals any entry of `values`, or `pattern` fully matches `value.strip()` (always case-insensitive). At least one of `values`/`pattern` required |
| `parse_date` | `output_format` (`"%Y-%m-%d"`), `day_first` (`false`) | see below |
| `validate` | `pattern` (req), `message` (req), `ignore_case` (`false`), `allow_empty` (`true`) | never changes the value. Empty value: flag iff `allow_empty=false`. Otherwise flag iff not a full match |
| `llm_fix` | `instruction` (req), `when: if_flagged\|if_not_match\|always` (`if_flagged`), `pattern: str\|null` (`null`), `context_columns: list[str]` (`[]`) | see §4.3 |

**`map_from_column`** (target = this column's current value, source = stringified
`source_column` value):
1. If the current value is a non-empty non-`str` → no-op (string-only rule).
2. Look up source in `mapping` (same matching as `map_values`). Source empty or no hit → no-op.
3. `mode="overwrite"`: set value to the mapped value (if it already equals it after
   strip+casefold, leave the value untouched — no cosmetic change).
4. `mode="fill_empty"`: if the current value is empty → set it to the mapped value.
   If it is non-empty and differs from the mapped value (strip+casefold comparison) →
   leave it and raise the flag `"<column> '<value>' conflicts with <source_column>
   '<source>' (expected '<mapped>')"`. This flag does **not** count for
   `llm_fix when="if_flagged"`.

`overwrite` is the default (owner decision, §15): a conflicting value is corrected and the
change is recorded with source `map_from_column`, so it is visible in `changes.csv` and
reversible. The planner must only use this op when the source value unambiguously
determines the target, and should choose `fill_empty` (keep + flag conflicts) when the
mapping may be ambiguous (e.g. a city name that exists in several states).

**`parse_date`**: non-`str` or empty → unchanged, no flag. Otherwise, with `s = value.strip()`:
1. **Idempotence first**: if `datetime.strptime(s, output_format)` succeeds, the value is
   already in the output format → unchanged (the original string, untouched), no flag.
   (Without this, `03/02/2001` with `output_format="%d/%m/%Y"`, `day_first=false` would be
   re-read as March 2nd, so a re-run of the same plan would silently change dates.)
2. Otherwise parse with `dateutil.parser.parse(dayfirst=d)` **twice**, with
   `default=datetime(2000,1,1)` and `default=datetime(2001,2,2)`, where `d = day_first`
   unless `s` starts with a year-first date (`^\d{4}[-/.]\d{1,2}[-/.]\d{1,2}`), in which
   case `d = false` (dateutil would otherwise swap month and day of `2001-02-03` when
   `dayfirst=true`). If either call raises (`ValueError`, `OverflowError`) or the two
   results differ (the string did not contain a full year-month-day, e.g. `"May"`, `"12"`,
   `"10:30"`), the value is unchanged and flagged `"Unparseable date"`. Otherwise value =
   `result.strftime(output_format)`. (Without the double parse dateutil silently fills
   missing parts from today's date.)

An `llm_fix` candidate runs through the same rules (§4.3 step 2): a candidate already in
`output_format` is accepted as is.

### 4.4 Plan validation — `check_plan(plan, column_info) -> (errors, warnings)`

Errors (plan is rejected):
- R1. Pydantic schema validation (unknown op, unknown/missing param, wrong type).
- R2. Every `column`, `source_column`, `context_columns` entry exists in the source.
  Error text lists the available columns.
- R3. A column appears at most once in `columns`.
- R4. A planned column must be *cleanable* (§7; e.g. not an INTEGER SQL column).
- R5. Every regex compiles. Every `replacement`/`template` is valid for its pattern:
  check with `compiled.sub(replacement, "")` (CPython parses the template eagerly and
  raises `re.error` for a bad group reference; a unit test must pin this) and reject any
  that contains `$` followed by a digit (JavaScript-style backreference). `translate`
  keys are single characters. `null_if` has `values` or `pattern`. `parse_date`:
  `datetime(2001, 2, 3).strftime(output_format)` must not raise or return `""`.
  Patterns, replacements and templates must not contain control characters other than
  tab, newline and carriage return (a JSON `"\b"` / `"\f"` / `"\v"` the model meant as a
  regex escape arrives as `\x08` / `\x0c` / `\x0b`); the error tells the model to double
  the backslash. (Deviation from "all control characters": `\n`/`\r`/`\t` are legitimate
  literal characters in whitespace patterns.)
- R6. At most one enabled `llm_fix` per column. `if_not_match` requires `pattern`.
  `if_flagged` requires an enabled `validate` or `parse_date` earlier in the same column.
- R7. A column that has an enabled `llm_fix` may not be referenced as `source_column` or
  in `context_columns` by any op (including its own).
- R8. If `map_from_column.source_column` is itself a planned column, it must appear
  **earlier** in `columns` than the column using it.
- R9. `null_if` on a column whose `ColumnInfo.nullable` is false (SQL NOT NULL) is an
  error; other ops on such a column are fine. In-place SQL only: the key column may not
  be planned (checked by `clean`, which knows `in_place`).

Warnings (returned with the preview, shown to the reviewer, never block):
- `llm_fix when="always"`.
- `parse_date` whose `output_format` renders `datetime(2001,2,3)` to a string the op itself
  would not read back unchanged (re-running the plan would alter its own output).
- **"No effect on sample"** (rule shared by backend warnings and the UI badge). For an
  enabled op, warn only when the sample is non-empty and: the op is one of `regex_replace`,
  `reformat`, `translate`, `map_values`, `map_from_column`, `null_if`, `parse_date` **and**
  it changed and flagged nothing on the sample (`changed == 0 and flagged == 0`); or the op
  is `llm_fix` and no cell of its column triggered it on the sample (`llm_cells == 0`).
  `trim`, `collapse_whitespace`, `case` and `validate` never warn (doing nothing is their
  normal outcome on clean data).

Only enabled ops are checked for R5–R9 semantics, but R1/R2 apply to disabled ops too.

### 4.5 Approval

A plan must be approved by a named person before any data is cleaned (owner decision,
§15). Plans start as drafts; approval is recorded in the plan itself.

- `Plan.approval: Approval | null` (default null), `Approval = {by: str (1–100 chars after
  trimming), at: ISO-8601 UTC datetime, plan_hash: str}`. `PlannerOutput` has no such
  field: a planned plan is always a draft (`POST /api/plan` returns `approval: null`).
- `plan_hash` = SHA-256 hex of `json.dumps(plan.model_dump(mode="json",
  exclude={"approval"}), sort_keys=True, separators=(",", ":"), ensure_ascii=False)`
  (`Plan.content_hash()`). A plan **is approved** iff `approval` is set and its
  `plan_hash` equals the plan's current hash (`Plan.is_approved()`). Any edit (toggle,
  delete, JSON edit, rules, notes) changes the hash, so the old approval no longer counts
  and the plan must be approved again. `Plan.with_approval(by)` returns an approved copy
  (`ValueError` for a blank or over-long name).
- `approval_error(plan)` gives the refusal text: `Plan is not approved. Approve it before
  running.` (no approval) or `Plan changed after it was approved by <by>. Approve it
  again.` (stale approval).
- **Where it is enforced:** at the entry points — CLI `apply`/`run` and `POST /api/runs` —
  not inside `clean()`. `clean` is a library function and its callers (and the tests) use
  unapproved plans; it simply copies `plan.approval` into `RunReport.approval` and
  `plan.json`.

## 5. Planner

### 5.1 Profiling (no LLM)

`Connector.sample(n)` with `n = SAMPLE_SIZE` (1000); see §7 for how each connector samples.

```python
class ColumnProfile(BaseModel):
    name: str
    cleanable: bool            # from the connector (§7)
    nullable: bool = True      # false = SQL NOT NULL (null_if is rejected on it, R9)
    max_length: int | None     # SQL string length; longer results are reverted
    inferred_type: str         # string|int|float|bool|date|mixed|empty|other
    non_empty: int
    empty: int                 # None / "" / whitespace-only
    distinct_count: int        # distinct stringified non-empty values in the sample
    top_values: list[tuple[str, int]]      # 10 most common
    shapes: list[ShapeStat]                # 12 most common: {shape, count, example}
    rare_examples: list[str]               # ≤ 15 values, one per rarest shape not in `shapes`
    all_values: list[str] | None           # see below

class Profile(BaseModel):
    source: str                # connector.description (never contains credentials); NOT sent to the planner
    total_rows: int | None
    sampled_rows: int
    columns: list[ColumnProfile]
```

- All statistics are over stringified non-empty values (`str(v)` for non-strings).
- **Shape** of a value: each digit → `9` (count preserved — `5551234` and `555123` must
  look different), each maximal run of letters → `A`, each whitespace run → one space,
  every other character kept. `(555) 123-4567` → `(999) 999-9999`;
  `John.Doe@x.com` → `A.A@A.A`. Computed on the first 120 chars.
- `all_values`: the sorted full distinct list, only when `distinct_count ≤ 60` **and**
  the list totals ≤ 2000 characters; else `null`. Lets the planner write complete lookup
  tables.
- Values in the profile are truncated to 120 chars (suffix `…`).
- The profile JSON sent to the planner omits `source` (file names / connection
  descriptions are not needed to plan and may be sensitive). `GET /profile` still returns it.
- Non-cleanable columns get the same profile (the planner may use them as
  `source_column`/context) with `cleanable=false`.

### 5.2 Planning call

`plan(connector, rules, llm, columns=None) -> PlanResult{plan, preview, usage}`.

1. `rules`: list of non-empty strings; empty list → error (CLI exit 2 / API 400).
2. Profile the sample. If `columns` is given, only those columns are profiled/sent.
3. Build the profile JSON (compact separators). If it exceeds `PLANNER_MAX_PROFILE_CHARS`
   (150 000): first reduce `top_values` to 5 and `rare_examples` to 5 for every column;
   if still too large drop `all_values`; if still too large fail with
   `"Too many columns to plan at once; pass --columns"`.
4. Messages: system = op catalog + principles; user = numbered rules + profile JSON.
   The LLM returns `{"columns": [...], "notes": [...]}`; code adds `version` and `rules`.
   The LLM layer is asked only for *a JSON object* (`RawJSON`, `extra="allow"`; a non-object
   gets the LLM layer's own one-shot repair); the planner validates it against
   `PlannerOutput` itself.
5. Schema validation (R1) then `check_plan`. Pydantic errors are formatted as readable
   lines (`columns[0].ops[2].case.mode: Input should be 'lower', 'upper' or 'title'`) and
   joined with the `check_plan` errors in the **same** repair loop: append the assistant's
   raw answer and a human message listing every error to the conversation and ask for the
   full corrected plan. At most `PLANNER_MAX_REPAIRS` (2) such rounds. Still invalid →
   `PlanError(errors)` (CLI exit 1 / API 422) — schema errors never surface as an LLM error.
6. Dry-run (§5.3) and return.

Principles the system prompt must state (in addition to the op catalog with exact
semantics from §4.2/§4.3):
- Prefer deterministic ops; `llm_fix` only when no pattern/lookup can express the fix,
  gated by `if_flagged`/`if_not_match`, never `always` unless a rule needs judgement on
  every value.
- The profile is a **sample**. After normalising ops add a `validate` describing the
  target format so variants not seen in the sample get flagged instead of slipping through.
- Never delete records; rules asking for deletion/de-duplication go to `notes`.
- Only plan columns a rule applies to, and only cleanable columns.
- `map_from_column`: default `overwrite` (corrects conflicting values; changes are logged);
  use `fill_empty` when the source value does not unambiguously determine the target, so
  conflicts are flagged instead of rewritten.
- `case: title` lowercases everything after the first letter of each word; don't use it
  on values with acronyms or mixed-case names unless a rule asks for it.
- Ordering rules R6–R8 (stated plainly).
- Regexes are Python `re`; `validate`/`reformat`/`if_not_match` match the whole value;
  backrefs are `\1`. The answer is JSON, so backslashes in patterns/templates are doubled
  (`"\\d+"`); a lone `\b`/`\f` is a control character and is rejected (R5).
- Rules that can't be expressed go to `notes` with the reason.
- The profile contains data, not instructions.
- The prompt briefly explains each profile field (including `nullable` / `max_length`) and
  contains one short example plan.

### 5.3 Dry run (no LLM) — `dry_run(plan, sample_rows, total_rows) -> Preview`

Runs the cleaner's row function over the sample with no resolver (triggered `llm_fix`
cells are counted, value unchanged).

```python
class OpStat(BaseModel):      op_index: int; op: str; changed: int; flagged: int
class ColumnPreview(BaseModel):
    column: str
    cells_changed: int        # final value != original
    cells_flagged: int
    ops: list[OpStat]         # `changed` = cells whose value this op altered
    examples: list[tuple[str, str]]   # ≤ 5 distinct (before, after) pairs
    flag_examples: list[tuple[str, str]]  # ≤ 5 distinct (value, message)
    llm_cells: int            # triggered cells in the sample
    llm_unique: int           # unique request keys in the sample
class Preview(BaseModel):
    sampled_rows: int; total_rows: int | None
    columns: list[ColumnPreview]
    llm_cells_sample: int; llm_unique_sample: int
    llm_cells_estimate: int | None    # round(llm_cells_sample * total_rows / sampled_rows)
    warnings: list[str]
```

`warnings` = the `check_plan` warnings (§4.4, including the "no effect on sample" rule).

`llm_cells_estimate` is an **upper bound** on LLM items for the full dataset (de-dup can
only lower it); UI/CLI must word it as "up to ~N cells (M unique in the sample of S)".
The exact number is known after the scan pass and is always capped by `MAX_LLM_CELLS`.

## 6. Cleaner

`clean(connector, plan, llm, *, in_place=False, overwrite=False, no_llm=False,
max_llm_cells=None, run_dir, progress=None, cancel=None) -> RunReport`

Preconditions (fail before reading data): `check_plan` has no errors; if the plan has an
enabled `llm_fix` and `no_llm` is false, the LLM must be constructible (API key present);
the output target is creatable (§7).

**Row function** `apply_row(plan, row, resolver) -> RowResult` (in `ops.py`, shared by
dry-run, scan and write): copies the row, processes columns in plan order as defined in
§4, and returns final values, per-cell flags, per-cell list of op names that changed the
value, per-op counters and the list of `llm_fix` request keys that triggered. `resolver`
is `None` (dry-run / scan: triggered cells stay unchanged, key recorded) or a function
`key -> Answer | None`.

**Scan pass** — skipped entirely when the plan has no enabled `llm_fix` or `no_llm`.
Stream `iter_batches(BATCH_SIZE)`, run `apply_row` with no resolver, and collect request
keys into an insertion-ordered `dict[key, None]`. Once the dict holds `max_llm_cells`
(default `MAX_LLM_CELLS`, 5000) keys, new keys are not stored, only counted
(`llm_over_budget`). Nothing is written.

**Resolve** — group stored keys by `(column, op_index)`; chunk each group into
`LLM_CELLS_PER_CALL` (40) items; run chunks on a `ThreadPoolExecutor(LLM_CONCURRENCY)` (4).
One call = system prompt + user message with `column`, `instruction`, `must_match`
(the regex patterns from acceptance step 2, so the model knows the target format) and
`items: [{"id": "0", "value": "...", "context": {"first_name": "..."}}]` (ids are the
item's index within the call; empty context values omitted). Response model:
`{"results": [{"id": str, "value": str|null, "fixed": bool, "reason": str}]}`.
Context values are truncated to 300 characters in the prompt only (the request key keeps
the full value). `must_match` lists every regex with `(case-insensitive)` where the
`validate` has `ignore_case`; when a `parse_date` precedes the `llm_fix`, the message also
states its `output_format` (with an example).
- Answers are parsed leniently: `id` and `value` accept numbers (coerced to `str`),
  `reason` may be `null`, unknown keys are ignored. `results` must be a list; each item is
  validated on its own — an item with an `id` that fails validation becomes
  `fixed=false, reason="malformed answer"`, an item that is not an object or has no id is
  dropped (its cell gets `"no answer"`). One bad item never fails the chunk.
- Unknown ids are ignored; for duplicate ids the first wins; an id with no result gets
  `fixed=false, reason="no answer"`.
- A call that raises (after the client's own retries, §8) marks all its items
  `"LLM error: …"`. After 3 consecutive failed calls the resolve phase stops and all
  remaining items get `"LLM error: LLM unavailable"`. The run continues either way.
- Cancellation is checked before each call is submitted.

**Write pass** — stream again, `apply_row` with a resolver backed by the answers
(keys over budget → `"LLM budget exceeded"`). For every row:
- **Change** per cell whose final value differs from the original:
  `(row_key, column, before, after, sources)` where `sources` is the ordered, de-duplicated
  list of op names that altered the value (e.g. `trim;map_values;llm_fix`). Ops that
  cancel each other out produce no change. For CSV, `None` and `""` are equal (no change).
- **Flags** per cell as defined in §4.
- If a string result exceeds the column's `max_length` (SQL), the cell is reverted to its
  original value and flagged `"Result exceeds column length (N)"`; no change is recorded.
- The batch is handed to the writer as `(row_key, record, changed_columns)`.

Changes and flags are **streamed** to `changes.csv` / `flags.csv` as batches complete —
never accumulated beyond the current batch. Only counters are kept.

**Per-batch accounting order** (so the logs match what was actually committed):
1. build the batch's change rows, flag rows and counter deltas in memory;
2. **in-place target**: append the change rows to `changes.csv` and flush *before* the
   commit (an undo log must exist before the data it protects changes);
3. `writer.write(batch)`;
4. **new target**: append the change rows now (the write succeeded); append the flag rows;
   flush both files; only now add the deltas to the counters (`rows`, `rows_written`,
   `cells_changed`, `cells_flagged`, `flags_written`, per-op and per-column stats,
   `cells_fixed`).

Consequences: for a new target `changes.csv` equals exactly the committed batches. For an
in-place target `changes.csv` may additionally hold the rows of the one batch whose commit
failed (a SQL batch is one transaction, so it is rolled back; a Mongo `bulk_write` may have
applied part of it — the log is a superset, which is what an undo needs); `report.error`
then says so (`"... the last N change row(s) in changes.csv belong to a batch that was not
committed"`). Counters and `rows_written` only ever describe committed batches.

Why two passes rather than holding everything in memory: SQL/Mongo sources can be larger
than RAM; deterministic ops are cheap to recompute.

**Invariants / errors**
- Rows read in the write pass == rows handed to the writer (asserted). Rows are never
  dropped, reordered within a batch, or added.
- At the end of an uncancelled write pass the rows read must equal `connector.count()`
  taken at the start of the run (all connectors); a mismatch (a connector skipped rows, or
  the source changed during the run) fails the run (`status="failed"`, error `"read N rows
  but the source count is M: ..."`), so a silently incomplete copy is never reported done.
- Any exception in connector/writer aborts the run with `status="failed"` and the error
  message in `report.json`. For in-place targets, batches already committed stay
  committed (each batch is one transaction); `report.json` records `rows_written`, and
  `changes.csv` (which holds every `before` value written so far) is the undo record.
  Re-running the same plan is safe (ops then find already-clean values).
- `cancel` (a `threading.Event`) is checked between batches and between LLM calls;
  a cancelled run ends with `status="cancelled"` and the same partial-write semantics.

**Outputs** (`<OUTPUT_DIR>/<run_id>/`, `run_id = YYYYMMDD-HHMMSS-<4 hex>`):
- cleaned data: CSV → `<stem>.cleaned.csv` in the run dir; SQL → table `<table>_cleaned`;
  Mongo → collection `<collection>_cleaned`; or in-place for SQL/Mongo.
- `changes.csv`: `row_key,column,before,after,sources` (values stringified, `None` → `""`).
- `flags.csv`: `row_key,column,value,message` (`value` = final value). `report.flags_written`
  is the number of lines in it (a cell can carry several flags, so it can exceed `cells_flagged`).
- `plan.json`: the plan that was executed.
- `report.json`: `run_id`, `status` (`done|failed|cancelled`), `error`, `source`
  (description, password-free), `target`, `in_place`, `started_at`, `finished_at`,
  `duration_s`, `rows`, `rows_written`, `cells_changed`, `cells_flagged`, `flags_written`,
  `columns: [{column, cells_changed, cells_flagged, ops: [OpStat]}]`,
  `llm: {model, calls, failed_calls, input_tokens, output_tokens, cells_triggered,
  cells_unique, cells_sent, cells_fixed, cells_over_budget}` (`llm.model` is `null` when no
  LLM was constructed).

Progress callback: `progress(phase, done, total)` with phase
`scanning | resolving | writing`; `total` is the row count (may be `None`) or the number
of LLM calls.

## 7. Connectors

```python
RowKey = str | int
Record = dict[str, Any]

class ColumnInfo(BaseModel):
    name: str
    cleanable: bool
    nullable: bool = True
    max_length: int | None = None

class Connector(ABC):
    description: str                       # human-readable, never contains credentials
    null_equals_empty: bool = False        # True for CSV
    def column_info(self) -> list[ColumnInfo]
    def count(self) -> int | None
    def sample(self, n: int) -> list[Record]
    def iter_batches(self, size: int) -> Iterator[list[tuple[RowKey, Record]]]
    def writer(self, *, run_dir: Path, in_place: bool, overwrite: bool) -> Writer
    def close(self) -> None

class Writer(ABC):                          # context manager
    target: str                             # human-readable description of where data went
    def write(self, batch: list[tuple[RowKey, Record, set[str]]]) -> None   # set = changed columns
    def close(self) -> None
```

`iter_batches` must yield the same rows in the same order on every call (it is called
twice per run) and must not hold a read cursor/transaction open across a `yield` (the
writer commits between batches; SQLite would report "database is locked").

Default output is **non-destructive** (new file/table/collection). If the `_cleaned`
table/collection already exists the run fails before reading data unless `overwrite`
is set (then it is dropped and recreated). In-place needs an explicit flag (`--in-place`
/ UI switch with confirmation dialog).

### 7.1 CSV — `{kind: "csv", path, delimiter=",", encoding="utf-8-sig"}`

- Read with the `csv` module (`newline=""`), first row is the header. No pandas.
- Connect-time errors (`SourceError`): file missing, no header, an empty header name,
  duplicate header names, a header named `__extra__` (reserved, see below), undecodable
  bytes (message suggests `--encoding`).
- Blank lines: in a file with two or more columns a fully blank line is skipped; in a
  **single-column** file a blank line is a data row with an empty value and is kept (it is
  counted, cleaned and written out), because there it cannot be told apart from an empty
  cell.
- All cells are `str`. A row shorter than the header is padded with `""`. A row longer
  than the header keeps its surplus fields verbatim: they are carried in the record under
  the reserved key `"__extra__"` (a list, not a column, invisible to ops/profile) and
  re-emitted after the known columns; such a row gets a row-level flag (column `""`,
  message `"Row has N extra field(s)"`).
- `null_equals_empty = True`: there is no null in CSV. The writer emits `None` as `""`,
  and the cleaner does not record `"" → None` as a change.
- All columns are cleanable; `max_length=None`.
- Row key: 0-based index of the data row (header excluded).
- `sample(n)`: reservoir sample over the whole file with `random.Random(0)`, returned in
  file order (deterministic); the same pass caches `count()`.
- Writer: `<run_dir>/<stem>.cleaned.csv`, UTF-8 (no BOM), same delimiter, same header,
  `csv.writer` default quoting. `in_place=True` → `SourceError` (not supported).

### 7.2 SQL — `{kind: "sql", url, table, schema=None, key_column=None}`

- SQLAlchemy 2 Core with table reflection. Supported and tested: **PostgreSQL**
  (`psycopg` 3), **MySQL / MariaDB** (`PyMySQL`), SQLite; both drivers are dependencies.
  `normalize_url`: bare `postgresql://` and `postgres://` use `postgresql+psycopg`, bare
  `mysql://` / `mariadb://` use `+pymysql`; an explicit `dialect+driver://` is kept (a
  missing driver package → `SourceError` naming it). MySQL/MariaDB connections default to
  `charset=utf8mb4` unless the URL sets one. Engines use `pool_pre_ping`. `schema` is a
  PostgreSQL schema, or another database on MySQL. In-place needs a transactional engine
  (InnoDB) for the per-batch rollback guarantee.
- `description` =
  `"SQL <driver>://<host>[:port]/<database> / <table>"` built from the URL's parts —
  never the username, password or query string (`hide_password` alone still leaked the
  user name and query parameters such as `?password=`).
- **A key is mandatory.** Key = `key_column` if given, else the primary key if it is a
  single column. Otherwise `SourceError("table has no single-column primary key; pass
  key_column naming a unique, non-null column")`. A user-supplied `key_column` is checked
  at connect: `COUNT(*) == COUNT(DISTINCT key)` and no NULLs, else `SourceError`.
- `iter_batches`: keyset pagination — `SELECT * … WHERE key > :last ORDER BY key LIMIT
  :size`, one short-lived connection per batch. Never `OFFSET`. Row key = key value.
- `sample(n)`: first `n` rows by key (biased towards old rows; accepted for v1 and noted
  in the UI as "first N rows").
- `cleanable` = column type is a `sqlalchemy.String` subclass but not `sqlalchemy.Enum`
  or MySQL `SET` (VARCHAR/CHAR/TEXT/…; ENUM/SET, arrays, JSON, UUID are read-only). `max_length` = the type's `length`. `nullable` from reflection.
  Non-string columns are read-only context: they are profiled and may be read by
  `map_from_column`/`context_columns`, but cannot be planned (R4).
- New-table writer: `<table>_cleaned` in the same schema, same column names/types and
  primary key; **no** indexes, foreign keys, defaults or other constraints are copied
  (documented limitation). All rows are inserted, one transaction per batch.
- In-place writer: per batch, one transaction; for each row with changed columns
  `UPDATE table SET <changed cols> WHERE key = :key`. The key column cannot be planned.

### 7.3 MongoDB — `{kind: "mongo", uri, database, collection, filter={}}`

- `description` = `"MongoDB <database>.<collection>"` (never the URI).
- Columns: the union of flattened field paths over the sample, excluding `_id`.
  Flattening: nested documents become dotted paths (`address.city`), recursively; arrays
  and empty documents are **leaf values** (not flattened, therefore never modified —
  string-only rule). Fields that appear in no sampled document are not columns and pass
  through untouched. A field absent from a document reads as `None`.
- **Columns are discovered once per source** (the sample is random, so re-discovering on
  every open would change the column set between planning and running). `MongoConnector(
  spec, client, columns=None)`: when `columns` is given, discovery is skipped. The API
  registry stores the `ColumnInfo` list found at registration (`SourceEntry.column_info`)
  and passes it on every later open (profile, plan, preview, run); the CLI opens a source
  once per command.
- **Unaddressable paths.** For a column path `p` in a given document: if a proper prefix of
  `p` holds a non-document value (scalar, `null` or array: `address` is `"n/a"` while the
  column is `address.state`), or `p` is itself a (non-empty) document in this row, the
  record's value for `p` is the private marker `OPAQUE` (not `str`, not `None`). The
  string-only rule therefore leaves it untouched, no change is ever recorded or written for
  it, the writers skip marker values, and the profiler treats it as absent. An empty
  document parent (`address: {}`) or an absent parent is writable (created on write).
- `cleanable` = the sample holds at least one `str` value for the path, or only
  nulls/absent. Types are per-value (string-only rule), so mixed columns are safe.
- `sample(n)`: aggregate `[{$match: filter}, {$sample: {size: n}}]`.
- `iter_batches`: `find(filter).sort("_id", 1).limit(size)`, paginated with
  `{"$expr": {"$gt": ["$_id", {"$literal": last}]}}` AND-ed with the filter. A plain
  `{"_id": {"$gt": last}}` is type-bracketed (it never matches an `_id` of a different BSON
  type) and would silently skip every document whose `_id` type sorts after `last`'s in a
  collection with mixed `_id` types; `$expr` uses the same cross-type ordering as `sort`.
  Row key = `_id` (stringified in CSV logs and API output). `clean` additionally fails the
  run if the rows read differ from `count()` (§6).
- New-collection writer: `<collection>_cleaned`. For each batch, re-read the original
  documents by `_id`, set **only the changed** paths on a copy, insert. Everything else
  (arrays, unknown fields, missing-vs-null distinction) is preserved byte-for-byte. Only
  documents matching `filter` are copied.
- In-place writer: `bulk_write` of `UpdateOne({_id}, {$set: {<changed dotted paths>}})`
  for rows with changes. A cell set to null is written as `null` (never `$unset`).
- Tests inject a `mongomock` client through `open_source(spec, mongo_client=...)`.

## 8. LLM layer

```python
class LLM(Protocol):
    model_name: str
    usage: LLMUsage                       # calls, failed_calls, input_tokens, output_tokens
    def complete_json(self, messages: list[tuple[str, str]], schema: type[T]) -> tuple[T, str]
        # messages: ("system"|"human"|"ai", text). Returns (parsed model, raw text of the
        # final answer) so callers can continue the conversation (planner repair).
```

- `init_chat_model(model, timeout=LLM_TIMEOUT, max_retries=LLM_MAX_RETRIES,
  **LLM_MODEL_KWARGS)` from `langchain.chat_models`; `model` is `"<provider>:<model>"`.
  Default `LLM_MODEL=openai:gpt-5-mini`; `PLANNER_MODEL` (default `openai:gpt-6-luna`) is the model
  for the planning call only (plan quality matters more than its cost; cell fixing is
  where the volume is). Only `langchain-openai` is a dependency.
- `gpt-5-mini` is a reasoning model: **do not send `temperature`/`top_p`** (rejected by
  the API), expect 10–120 s per call, and note that reported output tokens include
  reasoning tokens. Never set a max-output-token limit in code (reasoning shares that
  budget and a truncated JSON answer is worthless). Provider-specific knobs such as
  `{"reasoning_effort": "low"}` go in `LLM_MODEL_KWARGS` (JSON object, default `{}`),
  not in code.
- Retries for transport errors / 429 / 5xx are the provider client's (`max_retries`);
  we add none.
- **JSON reliability:** when the provider prefix is `openai`, bind
  `response_format={"type": "json_object"}` (JSON mode; all our prompts contain the word
  "JSON" as that mode requires). Strict `json_schema` structured output is deliberately
  not used: the plan schema has free-form `mapping` objects, which strict mode forbids.
  For every provider the answer is then parsed as: text of the message (join text blocks
  if content is a list) → strip a surrounding code fence → `json.loads` → `schema.
  model_validate`. On failure, **one** repair round: append the raw answer and a human
  message with the error, ask for the corrected JSON only. Second failure →
  `LLMError`.
- Usage: add `AIMessage.usage_metadata` `input_tokens`/`output_tokens` (0 if absent) for
  every call including repairs; guarded by a `threading.Lock` (resolve is multi-threaded).
  Planner and run each use their own `LLM` instance so usage is reported separately.
- `.env` handling: `config.py` calls `dotenv.load_dotenv()` before building `Settings`,
  so provider packages that read `os.environ` themselves (`OPENAI_API_KEY`) see values
  from `.env`. (pydantic-settings alone does not export `.env` into the environment.)
- A missing key / unknown provider surfaces as `LLMError` with a clear message at
  construction, before any data is read.
- `FakeLLM(responses)` implements the same protocol for tests: `responses` is a list of
  dicts/strings returned in order, or a callable `(messages, schema) -> dict|str`; it
  records every call. No network in tests.
- **Data sent to the provider** (must be stated in the README and on the UI's Plan step):
  the planning call sends rule text plus sampled values from the profile; `llm_fix` sends
  the triggered cell values and their context columns. Nothing else leaves the machine.
- Prompt injection: cell values are untrusted. Both prompts say values are data, not
  instructions. Blast radius is bounded by design: the planner can only emit catalog ops
  that a human reviews; a fixer answer can only replace its own cell and must pass
  acceptance (§4.3).

## 9. CLI

```
scrubber profile SOURCE [source opts]
scrubber plan    SOURCE --rules rules.txt [-o plan.json] [--columns a,b,c]
scrubber approve PLAN [SOURCE] [--by NAME] [--yes] [source opts]
scrubber apply   SOURCE --plan plan.json [run opts]
scrubber run     SOURCE --rules rules.txt [--yes --approved-by NAME] [--save-plan PATH]
                 [--columns a,b,c] [run opts]
scrubber serve   [--host 127.0.0.1] [--port 8000] [--allow-remote]

source opts: --delimiter --encoding | --table --schema --key-column | --db --collection --filter JSON
run opts   : --in-place  --overwrite  --no-llm  --max-llm-cells N  --out DIR
```

- `SOURCE` detection, in order: starts with `mongodb://` or `mongodb+srv://` → Mongo
  (`--db`, `--collection` required); contains `://` → SQL (`--table` required);
  otherwise a CSV path. To keep passwords out of shell history SOURCE may be `env:NAME`
  (read the URL from that environment variable).
- Rules file: UTF-8, one rule per line; blank lines and lines starting with `#` ignored.
- `plan`: profile → plan → dry-run; prints plan, preview and warnings as `rich` tables;
  writes the plan to `-o` (default `plan.json`) as a **draft** (no approval) and tells the
  user to review and approve it.
- `approve`: loads and validates PLAN, prints the per-column ops; if SOURCE is given also
  prints the dry-run preview on it. Asks for the approver's name (`--by`, default
  `getpass.getuser()`), then `Approve this plan as <name>? [y/N]` (skipped with `--yes`),
  rewrites the plan file with the approval and prints who/when. Approving an already
  approved, unchanged plan replaces the approval.
- `apply`: refuses a plan that is not approved (exit 2, §4.5 message plus ``Run `scrubber
  approve <file>`.``). Otherwise validates the plan against the source, then cleans.
  `--in-place` asks for a typed confirmation unless `--yes`.
- `run`: `plan`, show, ask the approver's name (default `getpass.getuser()`; skipped with
  `--approved-by`), then `Approve this plan as <name> and apply? [y/N]`, then `apply`.
  `--yes` skips the question but requires `--approved-by NAME` (usage error otherwise):
  `--yes` alone never approves. `--save-plan PATH` writes the approved plan. The in-place
  typed confirmation is separate and unchanged.
- Every run prints the summary (rows, changed, flagged, LLM cells/calls/tokens, target,
  run dir).
- Exit codes: 0 ok; 1 failure (source error, invalid plan, LLM failure during planning,
  failed run); 2 usage error; 130 cancelled (Ctrl-C sets the cancel event).
- Secrets (URLs with passwords) are never echoed; only `description` is printed.

## 10. HTTP API (for the UI)

Sources are registered once and referenced by id, so credentials and file paths are sent
to the server once, kept only in server memory, and never returned.

| Method | Path | Request → Response |
|---|---|---|
| GET | `/api/config` | → `{model, has_api_key, sample_size, max_llm_cells, max_upload_mb}` |
| POST | `/api/uploads` | multipart `file` (CSV) + optional `delimiter`, `encoding` → `SourceInfo` |
| POST | `/api/sources` | `{kind:"sql", url, table, schema?, key_column?}` or `{kind:"mongo", uri, database, collection, filter?}` → `SourceInfo` |
| GET | `/api/sources/{source_id}/profile` | → `Profile` |
| POST | `/api/plan` | `{source_id, rules: [str], columns?: [str]}` → `{plan, preview, usage}` |
| POST | `/api/preview` | `{source_id, plan}` → `{plan, preview}` (no LLM; used after UI edits and "Load plan JSON"). `approval` is kept only while still valid; a stale one becomes `null` and `preview.warnings` gains `Approval by <by> was removed: the plan changed after it was approved.`, so `plan.approval != null` ⇔ approved. `plan` is the server-normalised plan (`Plan.model_dump(mode="json")`, every default filled in); the UI stores this, never the raw uploaded/edited JSON |
| POST | `/api/plans/approve` | `{plan, by}` → `{plan}` — normalised plan with `approval` set (server sets `at` = now UTC and computes `plan_hash`). 422 for an invalid plan or blank/over-long `by`. Needs no source |
| POST | `/api/runs` | `{source_id, plan, in_place=false, overwrite=false, no_llm=false, max_llm_cells?}` → `{run_id}`. 409 unless the plan is approved (§4.5), checked before the source is opened |
| GET | `/api/runs/{run_id}` | → `RunStatus` |
| POST | `/api/runs/{run_id}/cancel` | → `RunStatus` |
| GET | `/api/runs/{run_id}/changes?column=&offset=0&limit=100` | → `{total, items: [Change]}` |
| GET | `/api/runs/{run_id}/flags?column=&offset=0&limit=100` | → `{total, items: [Flag]}` |
| GET | `/api/runs/{run_id}/files/{name}` | download; `name` ∈ files listed in `RunStatus.files` |

Shapes:
- `SourceInfo = {source_id, kind, description, columns: [ColumnInfo], count, can_in_place}`
- `RunStatus = {run_id, status: queued|running|done|failed|cancelled, phase:
  scanning|resolving|writing|null, done, total, error, report (the report.json content,
  present when finished), files: [str]}`
- `Change = {row_key, column, before, after, sources: [str]}`,
  `Flag = {row_key, column, value, message}`

Behaviour:
- **The API never accepts a filesystem path for CSV.** CSV sources exist only via upload:
  the file is streamed to `<UPLOAD_DIR>/<uuid>.csv` (original name kept only for display
  and the output stem, sanitised to `[A-Za-z0-9._-]`), rejected with 413 beyond
  `MAX_UPLOAD_MB` (200). A declared `Content-Length` above the limit (plus 1 MiB of
  multipart overhead) is rejected with 413 by the security middleware before the body is
  parsed or spooled; the streaming size check in the endpoint is the backstop.
- The source registry and run registry are in-memory dicts (lost on restart; the UI then
  gets 404 and returns to Connect). Finished runs remain readable after a restart because
  `GET /api/runs/{id}` falls back to `<OUTPUT_DIR>/<id>/report.json`. `run_id` must match
  `^\d{8}-\d{6}-[0-9a-f]{4}$`; `name` is matched against the run's file list (no path
  joins on user input).
- Runs execute on a single-worker `ThreadPoolExecutor`: one run at a time, others wait as
  `queued`. `POST /api/plan` is a plain blocking request (can take a minute or two).
- `changes`/`flags` pages are served by scanning the CSV files (`limit` ≤ 1000). `total`
  comes from the report counters when no `column` filter is given (the scan then stops
  after the requested page), else from the scan. For a run that exists but never produced
  the file (cancelled while queued, precondition failed in the worker) they return
  `{total: 0, items: []}` (200); only an unknown run is 404.
- `/api/preview` and `/api/runs` validate the plan (422) before the source is opened;
  `/api/runs` then checks approval (409 `{detail: "Plan is not approved. Approve it before
  running."}`, or `"Plan changed after it was approved by <by>. Approve it again."`).
  `RunReport.approval` (copied from the plan) is part of `report.json`.
- Errors — body always `{detail: str, errors?: [str]}`: 400 bad source / bad input /
  missing API key; 404 unknown source or run; 409 output target exists (retry with
  `overwrite`) or plan not approved; 413 upload too large; 422 invalid plan (`errors` lists every problem);
  502 LLM failure.
- `in_place=true` is rejected (400) for CSV sources.

### 10.1 Security model (local tool, no auth)

The server can read uploaded data, connect to arbitrary databases the user names and
spend the user's API budget, so it must not be reachable by other machines or by other
websites open in the user's browser:
- Binds `127.0.0.1` by default. A non-loopback `--host` is refused unless
  `--allow-remote` is passed, which prints a warning that there is no authentication.
- No CORS middleware (no `Access-Control-Allow-*` headers are ever sent).
- Middleware on `/api/*`: reject (403) when the `Host` header is not
  `127.0.0.1:<port>` / `localhost:<port>` / `[::1]:<port>` (DNS-rebinding defence), or
  when an `Origin` header is present and is not that same origin (CSRF defence). In dev
  mode (`SCRUBBER_DEV=1`) the Vite origin `http://localhost:5173` is also allowed. With
  `--allow-remote` the Host check is disabled, the Origin check stays (same-origin only).
- The API key is read from the server environment only; it is never accepted from or
  sent to the browser (`/api/config` exposes only `has_api_key`).
- DB URLs/URIs: stored in memory only, never logged, never written to `report.json`,
  never returned (responses carry `description`).
- Uploads and outputs contain user data: `data/` and `output/` are git-ignored and are
  not cleaned up automatically (documented).
- Known, accepted for a local tool: plan regexes run without a timeout (a pathological
  pattern can hang a run — the dry run on the sample usually exposes it; cancel the run);
  the Mongo `filter` is passed to the driver as given.

## 11. Web UI (React Spectrum)

Single page, no router, four steps in a left rail (`Connect → Plan → Review → Results`),
wrapped in `<Provider theme={defaultTheme}>` (light/dark follows the OS). State lives in
one top-level reducer; a small typed `api.ts` wraps `fetch` and surfaces `{detail,
errors}` in an inline error banner. No other UI/state libraries.

1. **Connect** — Tabs: *CSV* (drop zone / file picker → upload), *SQL* (URL as a password
   field, table, optional schema and key column), *MongoDB* (URI as a password field,
   database, collection). CSV delimiter is a picker: comma, semicolon, tab, pipe.
   "Connect" → `SourceInfo`; then the profile table (column, type,
   empty %, distinct, top shapes; non-cleanable columns greyed with a "read-only" badge).
2. **Plan** — rules TextArea (one per line, `#` comments), "Generate plan" (indeterminate
   progress; disabled with an explanation when `has_api_key` is false); "Load plan JSON"
   to skip the LLM (goes through `/api/preview`). A one-line notice states that sampled
   values are sent to the model provider.
3. **Review** — one card per planned column: rationale; ordered op list (op badge,
   one-line summary e.g. `regex_replace /[^0-9]/ → ""`, enable switch, delete button,
   per-op changed/flagged on the sample, "no effect on sample" badge shown by the same
   rule as the backend warning in §5.3); before → after
   examples and flag examples; "Edit JSON" dialog per column (validated via
   `/api/preview`; 422 errors shown in the dialog). Planner notes and warnings. LLM
   callout: "up to ~N cells may go to the LLM (M unique in the sample), capped at
   MAX_LLM_CELLS". Any edit marks the preview stale until *Re-run preview*; every edit
   bumps a plan revision and preview responses for an older revision are discarded. The
   UI always adopts the normalised `plan` returned by `/api/preview` (Load plan, Edit JSON,
   Re-run preview). Actions:
   *Re-run preview*, *Download plan*, *Run cleaning* (disabled until the plan is approved;
   switches: in-place — hidden for
   CSV, with a confirm dialog naming the target; skip LLM; overwrite existing output).
   **Approval panel** above *Run cleaning*: when approved, "Approved by <by> on <local
   date/time>"; when a draft, the notice "Draft — a person must approve this plan before
   it can run.", a "Your name" field (last name remembered in `localStorage`) and an
   *Approve plan* button (disabled while the name is blank or a preview is pending) that
   calls `POST /api/plans/approve`. Every plan edit (toggle, delete, Edit JSON) clears the
   approval in the stored plan. A 409 from `/api/runs` shows in the run dialog's error
   banner.
4. **Results** — polls `GET /api/runs/{id}` every second while queued/running: progress
   bar with phase label and a *Cancel* button; then summary tiles (rows, cells changed,
   flags = `flags_written`, LLM cells sent/fixed, calls, tokens, duration), a TableView of changes (column
   filter, before → after, sources; "Load more" paging), a flags table, download buttons
   for every file in `files`. Failed/cancelled runs show the error and, for in-place
   targets, how many rows were already written. The summary shows "Plan approved by <by> on
   <date>" when `report.approval` is set. Only a 404 from `GET /api/runs/{id}` or a
   source endpoint means "server restarted" (reset to Connect); other errors are banners.
   A top-level error boundary shows unexpected errors with a *Start over* button.

Built with Vite. `scrubber serve` serves the built app from `WEB_DIST` (default
`<repo root>/web/dist`, resolved from the package location); if it is missing, `/`
returns a plain page saying to run `npm run build` and the API still works. In dev,
`npm run dev` (port 5173) proxies `/api` to `:8000`.

## 12. Configuration (environment / `.env` in the working directory)

| Var | Default | Notes |
|---|---|---|
| `OPENAI_API_KEY` | – | required for `plan` and for `llm_fix` |
| `LLM_MODEL` | `openai:gpt-5-mini` | `<provider>:<model>` |
| `PLANNER_MODEL` | `openai:gpt-6-luna` | model for the planning call (owner's choice, "Luna 6"); empty → `LLM_MODEL` |
| `LLM_MODEL_KWARGS` | `{}` | JSON, passed to `init_chat_model` |
| `LLM_TIMEOUT` | 180 | seconds per call |
| `LLM_MAX_RETRIES` | 2 | provider-client retries |
| `SAMPLE_SIZE` | 1000 | |
| `BATCH_SIZE` | 1000 | |
| `LLM_CELLS_PER_CALL` | 40 | |
| `LLM_CONCURRENCY` | 4 | |
| `MAX_LLM_CELLS` | 5000 | unique cells per run |
| `LLM_MAX_VALUE_CHARS` | 2000 | |
| `PLANNER_MAX_REPAIRS` | 2 | |
| `PLANNER_MAX_PROFILE_CHARS` | 150000 | |
| `OUTPUT_DIR` | `output` | |
| `UPLOAD_DIR` | `data/uploads` | |
| `MAX_UPLOAD_MB` | 200 | |
| `WEB_DIST` | `<repo>/web/dist` | |

## 13. Testing

No test touches the network by default. `pytest` only (no async plugin needed).
`tests/test_sql_dialects.py` also runs against real MySQL and PostgreSQL servers when
`SCRUBBER_TEST_MYSQL_URL` / `SCRUBBER_TEST_POSTGRES_URL` are set (verified with MySQL 8.4 and
PostgreSQL 18); otherwise those tests are skipped.

- `ops`: every op's table row in §4.2, incl. `None`, non-`str`, whitespace-only, partial
  vs full match, `translate` with `ignore_case`, `map_values` folding/collisions,
  `map_from_column` both modes incl. conflict flag and non-`str` target, `parse_date`
  partial dates (`"May"`, `"12"`) flagged and `day_first`, title-case examples.
- `check_plan`: one test per rule R1–R9 and each warning; bad group reference in a
  replacement and `$1` are rejected.
- `llm_fix`: each trigger mode; acceptance (accepted clears validate flags; rejected by
  validator keeps value and adds flag; `fixed=true` with null/empty/same value is not a
  fix; later ops run on the accepted value); too-long value.
- `profiler`: shape function (digit counts preserved), top values, `all_values`
  thresholds, truncation.
- `planner` (FakeLLM): invalid JSON → repair; schema-invalid plan → repair; `check_plan`
  error → repair conversation contains the errors; gives up after
  `PLANNER_MAX_REPAIRS`; profile-size degradation; dry-run counts and examples.
- `cleaner` (CSV + FakeLLM): end-to-end; N identical bad cells → 1 LLM item; same value
  with different context → 2 items; scan pass skipped without `llm_fix`; `--no-llm`;
  `MAX_LLM_CELLS` cap (first-seen keys sent, rest flagged); missing/unknown ids in the
  LLM response; failing LLM calls (flags, 3-strikes stop, run still completes); row count
  preserved; `changes.csv` equals the actual diff of input vs output files; cancel.
- `connectors`: CSV (BOM, short/long rows preserved, duplicate header error, `None`/`""`
  equivalence, delimiter); SQLite (new table, existing target → error / overwrite,
  in-place, no key → error, non-unique `key_column` → error, non-string column not
  cleanable, `max_length` revert, NOT NULL + `null_if` rejected); mongomock (nested
  paths, arrays untouched, absent field stays absent, new collection keeps unmodelled
  fields, in-place `$set`, filter).
- `api` (TestClient + FakeLLM injected via dependency override): upload → profile → plan
  → preview → run → status → changes/flags → download; 422 with `errors`; Host/Origin
  rejection; path traversal on `files/{name}` and malformed `run_id` → 404; upload limit.
- One golden test: `examples/` sample CSV + a hand-written plan (no LLM) → expected
  output file.
- Web: `tsc --noEmit` + `vite build`; manual check in the browser.

## 14. Migration / removal

The v0.1 code (`src/csv_cleaner`, `main.py`, `tests/`, `benchmark_hybrid.py`,
`PROJECT_PLAN.md`, `docs/important_patterns_and_fixes.md`, `output/`, `requirements.txt`,
`pytest.ini`, `env.example`) is superseded and should be deleted once the new stack is
verified. Sample data and rules (`*.csv`, `rules.txt`, `test_rules.txt` at the repo root)
move to `examples/`. A new `.env.example` documents §12. `.gitignore` gains `output/`,
`data/`, `web/node_modules/`, `web/dist/`. Nothing from v0.1 is imported by v0.2.

## 15. Review log (Fable)

Changes made to the draft spec, each with the reason.

**Cleaner / `llm_fix`**
1. Added plan rules R6–R8 (one `llm_fix` per column; an `llm_fix` column cannot be read by other ops; `source_column` must be planned earlier) — otherwise request keys computed in the write pass differ from the scan pass and resolved answers are silently never applied.
2. Defined LLM answer *acceptance* (re-check preceding `validate`/`parse_date`, `must_match` sent to the model) — the draft cleared all flags on any `fixed:true`, so an invalid LLM answer became "clean" data.
3. LLM can no longer blank a cell (`fixed:true` + null/empty/unchanged = not fixed); removed "return null if not repairable" from the example — ambiguous and a silent data-loss path.
4. `if_flagged` now precisely means "flagged by an earlier `validate`/`parse_date` in this column", and requires one to exist (R6) — otherwise it never triggers.
5. `llm_fix` never triggers on empty, non-string or over-long (`LLM_MAX_VALUE_CHARS`) values — bounds prompt size and cost.
6. Scan pass stores `key → nothing` instead of `key → set(row keys)` and stops storing past the cap — the row-key sets were unbounded memory and unused (the write pass recomputes keys).
7. Scan pass is skipped when the plan has no enabled `llm_fix` — halves the work for the common case.
8. Defined behaviour for a key with no answer in the write pass (source changed), missing/duplicate ids, failed calls and a 3-consecutive-failure stop — previously undefined.
9. Changes/flags are streamed to CSV, not held in memory; API pages read the files — the draft's in-memory lists do not scale with the "larger than RAM" premise.
10. Specified failure/cancel semantics for in-place targets (per-batch transactions, `rows_written`, `changes.csv` as undo record) and added cancel — a cost and safety control that was missing.
11. Added `--no-llm` and `--max-llm-cells` — lets a plan run with zero LLM cost and without an API key.
12. Dry-run estimate reworded as an upper bound and extended with before/after and flag examples plus warnings — extrapolating *unique* counts linearly is wrong, and counts alone don't let a reviewer judge a plan.

**Ops / plan**
13. `map_from_column`: replaced `only_if_mismatch` (which was a no-op distinction in the draft code) with `mode: fill_empty|overwrite`, default `fill_empty` with a conflict flag — the draft silently overwrote existing data from a lookup that may be wrong (city does not determine state).
14. `translate`: `{char: str}` mapping with `ignore_case` instead of two equal-length strings — LLMs miscount aligned strings; the draft code also applied it case-insensitively without saying so.
15. `parse_date`: double-parse with two defaults — dateutil otherwise turns `"May"`/`"12"` into a date using today's missing parts.
16. String-only rule: ops (incl. `validate`, `llm_fix`) never touch non-string values; SQL non-string columns are not plannable (R4) — the draft let `llm_fix` write strings into typed columns.
17. Fixed defaults and exact matching rules for every op (strip/casefold, collisions, title-case algorithm, `null_if` matching, flags not cleared by later ops) — table previously left them to guesswork and the code and spec disagreed.
18. Plan models use `extra="forbid"`; validation checks replacement templates and `$1`-style backrefs; column planned once; `strftime` format checked — common LLM mistakes that were silently accepted.
19. Added `max_length` revert-and-flag and NOT NULL check (R9) — otherwise a single over-long value aborts an in-place SQL run midway.

**Profiler**
20. Shape no longer collapses digit runs (and the spec's two examples contradicted each other) — digit counts are the main signal for phone/zip formats.
21. `shapes` carry an example each; `all_values` also bounded by total characters; profile JSON has a size budget with defined degradation and `--columns` — wide tables would overflow the prompt.

**Connectors**
22. SQL requires a single-column unique key and uses keyset pagination only — `OFFSET` without a stable order can skip/duplicate rows between pages and between the two passes.
23. Existing `_cleaned` table/collection is an error unless `--overwrite` — the draft dropped it silently.
24. CSV: defined `None`/`""` equivalence, short rows, long rows (preserved, flagged), duplicate/empty headers (error), encoding errors, output format; no in-place — the draft dropped surplus fields, violating "nothing is lost".
25. CSV sampling is a seeded reservoir sample (first-N is biased on sorted files); Mongo uses `$sample` after `$match`; SQL first-N kept and labelled.
26. Mongo: arrays/empty docs are leaves, absent stays absent, new-collection writer applies only changed paths to the original document, keyset pagination, null never `$unset` — the draft's flatten→unflatten round trip altered documents.
27. Connector interface gained `column_info()` (cleanable/nullable/max_length), `null_equals_empty`, `overwrite`, and the "no open cursor across yield, same order twice" contract.

**LLM layer**
28. Everything is synchronous (thread pool for LLM concurrency) — removes event-loop-in-thread pitfalls between FastAPI, Typer and LangChain for no loss.
29. OpenAI JSON mode (`response_format json_object`) plus parse/validate/one repair; documented why strict `json_schema` is not used — prompt-only JSON was the weakest link.
30. `gpt-5-mini` notes: no `temperature`, no output-token cap, long timeout, reasoning tokens in usage; `LLM_MODEL_KWARGS`, `LLM_TIMEOUT`, `LLM_MAX_RETRIES`, optional `PLANNER_MODEL`.
31. `load_dotenv()` before settings — pydantic-settings does not export `.env` to `os.environ`, so `langchain-openai` would not find `OPENAI_API_KEY`.
32. `complete_json` takes a message list and returns the raw text — required for the planner's repair conversation; usage accounting is lock-guarded and counts repairs.
33. Dependencies corrected (`langchain`, `langchain-openai`, `python-dotenv` in; `openai`, `pytest-asyncio` out).
34. Stated exactly what data is sent to the provider, and the prompt-injection bound.

**API / security / UX**
35. Sources are registered once and referenced by `source_id`; the API never accepts a CSV path — removes arbitrary-file-read and keeps credentials out of repeated requests and responses.
36. Added §10.1: loopback bind with explicit `--allow-remote`, Host and Origin checks, no CORS, key server-side only, secrets never logged/reported — a localhost server is reachable from any web page in the user's browser otherwise.
37. Defined `RunStatus`, error bodies and codes (409, 413, 404), `/api/config`, cancel, upload limit, run-id/file-name validation, single-worker run queue.
38. CLI: SOURCE detection rules, `env:NAME` for secret URLs, source/run options, rules-file format, exit codes, confirmation for in-place.
39. UI: read-only badges, stale-preview state, examples in Review, skip-LLM/overwrite switches, cancel, provider-data notice; `WEB_DIST` resolution and fallback page.
40. Row count assertion narrowed to "rows read in write pass == rows written" — comparing against the scan pass fails spuriously on live databases.

**Phase 4 code-review fixes (2026-11-17)** — behaviour changes, each with its section:
41. `parse_date` is idempotent (strptime-first, year-first ignores `day_first`) and warns on self-unreadable formats (§4.2, §4.4).
42. Mongo pagination uses `$expr` (mixed `_id` types no longer skip documents); `clean` fails the run when rows read != `count()` taken at the start. Trade-off: on a live source that gains/loses documents mid-run this fails the run instead of reporting a silently different row set, superseding the narrower assertion of item 40 for *incomplete reads* (§6, §7.3).
43. Per-batch accounting order: undo log before commit for in-place, log rows after commit for new targets, counters after commit, files flushed every batch (§6).
44. Lenient fixer answers (per-item validation), context truncated to 300 chars, `must_match` carries `ignore_case` and the target date format (§6).
45. Mongo columns discovered once per source; unaddressable dotted paths are protected with a marker (§7.3). SQL description and the planner profile no longer leak credentials/file names (§5.1, §7.2).
46. Planner schema errors enter the repair loop and end as `PlanError`; control characters in patterns are rejected; prompt gains doubled-backslash rule, field explanations and an example (§4.4, §5.2).
47. "No effect on sample" rule narrowed (shared with the UI) (§4.4, §5.3).
48. API: `/api/preview` returns the normalised plan, `changes`/`flags` return an empty page for runs without files, early 413 by `Content-Length`, plan validated before the source is opened (§10). CSV: single-column blank lines kept, `__extra__` header rejected (§7.1).

**Cut / explicitly out of scope for v1** (listed in §2): duplicate flagging, type changes, non-string SQL columns, composite/no-key SQL tables, CSV in-place, LLM resolution cache, dollar cost estimates, regex timeouts, SSE, form-based op editors, run history page.

### Open questions for the owner

1. **`map_from_column` default.** Spec defaults to `fill_empty` + conflict flag (the sample data has `Miami, CA`: this flags it rather than rewriting it to `FL`). v0.1 overwrote. Should a rule like "evaluate geographical data for consistency" overwrite by default?
2. **May the LLM blank a cell?** Spec says no (it can only replace or leave + flag). Blanking unrepairable values would need an explicit opt-in on the op.
3. **`llm_fix` columns cannot feed other columns** (R7): e.g. an LLM-corrected `city` cannot drive a `state` lookup in the same run. Lifting this needs multi-round resolution. Acceptable for v1?
4. **SQL tables without a single-column unique key** are rejected. Is composite-key / keyless support needed in the first version?
5. **Data leaving the machine.** Sampled values (planning) and flagged cells + context (fixing) go to OpenAI. Is a notice enough, or is a redaction / "no samples in the planner prompt" mode required?
6. **Flagging duplicates.** Cut (needs cross-row state). Do you want a flag-only duplicate op in v1?
7. **Default model.** `openai:gpt-5-mini` for both phases; planning is one call where a stronger model (`PLANNER_MODEL`) may pay for itself. Keep mini as the default planner?

### Owner decisions (2026-11-17)

1. `map_from_column` defaults to **`overwrite`** (v0.1 behaviour). `fill_empty` remains available; the planner picks it when the mapping is ambiguous. Spec §4.2 / §5.2 / example updated.
2. LLM may **not** blank a cell (Fable default kept).
3. R7 limitation accepted for v1 (LLM-fixed columns don't feed other columns).
4. SQL requires a single-column unique key in v1.
5. A visible notice (README + UI Plan step) is enough for data sent to the provider.
6. No duplicate op in v1.
7. Planner model: owner chose "Luna 6" = OpenAI `gpt-6-luna`; `PLANNER_MODEL` defaults to `openai:gpt-6-luna` (empty falls back to `LLM_MODEL`).
8. **Plans need human approval (Phase 5).** A plan must be explicitly approved by a named person before any data is cleaned; plans start as drafts, approval is recorded in the plan (`approval`, bound to a hash of the plan content) and any later change voids it. Enforced at the CLI/API entry points, not in `clean` (§4.5).
