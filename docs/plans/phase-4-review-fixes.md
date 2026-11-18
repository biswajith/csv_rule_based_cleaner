# Phase 4 — Fixes from the Fable code review

Two agents work in parallel: **4A backend** (`backend/` only) and **4B web** (`web/` only).
Finding numbers refer to the Fable review (reproduced in summary below). Every fix gets
a regression test. The spec (`docs/SPEC.md`) stays the source of truth; where a fix
changes specified behaviour, update the spec section in the same change (4A owns spec
edits; 4B lists any spec changes it needs in its report instead).

## Shared API contract changes (both agents implement their side exactly)

C1. `POST /api/preview` response becomes `{plan, preview}` where `plan` is the
    server-normalised plan (`Plan.model_dump(mode="json")`, all defaults filled in). The
    UI stores this `plan`, never the raw uploaded/edited JSON. (Finding 5)
C2. `GET /api/runs/{id}/changes` and `/flags` return `{total: 0, items: []}` (200) when
    the run exists but the file does not (queued-cancelled, precondition failed in worker).
    404 only for an unknown run. (Finding 4)
C3. `RunReport` gains nothing new, but the UI must type `flags_written: number` and
    `llm.model: string | null`; the "Flags" tile shows `flags_written`. (Finding 19)
C4. "No effect on sample" rule (queued noise item + finding 13). A warning/badge is shown
    for an enabled op only when **all** hold: `changed == 0 and flagged == 0`; the op is
    one of `regex_replace, reformat, translate, map_values, map_from_column, null_if,
    parse_date`; (validate, trim, collapse_whitespace, case never warn). `llm_fix` warns
    only when the column's `llm_cells == 0` on the sample. Backend emits warnings by this
    rule; the UI badge uses the same rule (computed client-side from the preview).

## 4A — Backend

1. **parse_date idempotence (F1, high).** In `op_parse_date`: if
   `datetime.strptime(s, output_format)` succeeds → value unchanged, no flag. Else if the
   value matches `^\d{4}-\d{1,2}-\d{1,2}` (year-first) → parse with `dayfirst=False`
   regardless of `day_first`. Else the existing double-parse with `day_first`. Apply the
   same strptime-first logic to the candidate in `_accept`. `check_plan` warning when
   `output_format` renders `datetime(2001,2,3)` such that the op itself would read it
   back differently (i.e. re-parsing the rendered string with the op's own rules does not
   return 2001-02-03). Tests: idempotence for several formats × day_first, ISO input with
   day_first, LLM candidate through parse_date with day_first. Update spec §4.2.
2. **Mongo pagination (F2, high).** Paginate with `{"$expr": {"$gt": ["$_id", last]}}`
   combined with the filter (check mongomock supports it; if not, use a single sorted
   cursor per pass consumed in batches, closing nothing across yields is acceptable for
   Mongo — document the choice). Additionally the cleaner fails the run (`status=failed`,
   clear error) when rows read in the write pass != `connector.count()` for SQL/Mongo
   (skip for CSV where count is exact anyway — or apply to all, it's cheap). Test mixed
   `_id` types.
3. **Accounting order (F3).** Per batch: build change/flag rows in memory → for in-place
   targets write+flush the change rows first (undo log before commit) → `writer.write` →
   for new targets append rows after a successful write → bump counters only after the
   write succeeded. Flush both CSVs every batch. Test: writer fails on batch N →
   `changes.csv` equals exactly what was committed (new-target) / committed + at most the
   failing batch marked (in-place: add a `committed` column `yes|no`? Keep simpler: for
   in-place, write the failing batch's rows then, on failure, record `rows_written` and
   note in `report.error` that the last batch's changes.csv rows were not committed).
   Choose the simplest correct design and document it in spec §6.
4. **C1, C2** in `api.py`/`runs.py`, with tests.
5. **Lenient fixer items (F6).** `FixItem`: `id: str` (coerce), `value: str | None`
   (coerce numbers to str via before-validator), `fixed: bool = False`,
   `reason: str | None = None`, `extra="ignore"`. Items failing validation individually
   become `fixed=false, reason="malformed answer"` instead of failing the chunk (validate
   `results` as a list of dicts, then each item separately). Test with `reason: null`,
   numeric value, garbage item.
6. **Stable Mongo columns (F7).** Discover columns once at registration/open by the CLI or
   API and pass them back on later opens (`MongoConnector(spec, client, columns=...)`;
   `SourceEntry` stores `column_info`). Seed nothing else. Test stability across opens.
7. **Description leak (F8).** SQL `description` = `"SQL <driver>://<host>[:port]/<database>
   / <table>"` built from URL parts — no username, password or query string. Exclude
   `source` (and any description) from the profile JSON sent to the planner. Tests.
8. **Mongo mixed doc/scalar parents (F10).** In `_record`, a path whose parent in this
   document is a non-document value (scalar/null/array) yields a sentinel-free `None`
   **and** the path is marked non-writable for this row: simplest approach — the writer
   reverts by raising? No: implement in the connector by exposing per-row
   `readonly_paths` is too invasive. Do this: `set_path` failing in the new-collection
   writer or a non-document parent in the in-place writer → raise `SourceError` with a
   clear message *before* writing that batch? That aborts runs on messy data. Preferred:
   in `_record`, return the cell value as the original non-str leaf so the string-only
   rule leaves it untouched — i.e. for path `address.state` where `address` is `"n/a"`
   or `null`, the record holds a private marker object (not str, not None) so no op
   touches it, and the writers skip marker values. Document in spec §7.3. Tests for
   scalar and null parents, both writers.
9. **Planner schema repair (F11).** `planner.plan` asks `complete_json` for a raw dict
   model (e.g. `RawJSON(BaseModel, extra="allow")` or a dedicated method returning parsed
   JSON), validates `PlannerOutput` itself, and feeds pydantic errors (formatted as
   readable `column[i].ops[j]: …` strings) into the same repair loop as `check_plan`
   errors; ends in `PlanError` (422). Test.
10. **Prompts/validation (F12).** `check_plan` rejects `\x08`/`\x0c` (and other control
    chars except `\t`) in patterns/templates with a message hinting at doubled
    backslashes; planner prompt states backslashes must be doubled in JSON; add
    `nullable` and `max_length` to `ColumnProfile` (from `ColumnInfo`) so the prompt line
    about NOT NULL is actionable; `must_match` items carry `ignore_case` and, when a
    `parse_date` precedes, the fixer message states the target date format; explain
    profile fields briefly; add one short example plan to the planner system prompt.
11. **F13, C4** warnings rule in `planner.dry_run`. Tests.
12. **F14** truncate context values in the fixer prompt to 300 chars (not in the key).
13. **F15** reject uploads early in the security middleware using `content-length` when
    present (413); keep the streaming check as a backstop.
14. **F17** CSV: single-column files keep blank lines as empty records (multi-column
    files keep skipping fully blank lines); header named `__extra__` → `SourceError`.
15. **F18** parse/validate the plan before opening the source in API endpoints.
16. **F21 simplifications (cheap ones only):** `Plan.has_llm_fix()` used everywhere;
    replace the `id(op)` fold cache with a pydantic private attr computed lazily on the
    op; single flatten in Mongo `_discover`; dedupe `_s`/`_cell`/`stringify` helpers;
    early-exit page scan when unfiltered. Skip the CLI threading and `check_target`
    refactors unless trivial.

Done when: all tests pass (old + new), spec updated where behaviour changed, report lists
each finding → what changed → test name.

## 4B — Web

1. **C1** use the normalised plan from `/api/preview` for Load plan and Edit JSON
   (Edit JSON: send the full plan with the edited column replaced, then dispatch the
   returned plan). Add a top-level React error boundary showing the error and a "Start
   over" button.
2. **C2/F4** only a 404 from `GET /api/runs/{id}` (or source endpoints) means
   "server restarted"; a 404 elsewhere is a normal error banner.
3. **F9** plan revision counter in state; every edit increments it; preview responses
   carry the revision they were requested for and are dropped if stale.
4. **C3, C4** (badge rule, flags tile, types).
5. **F16** delimiter Picker: Comma `,`, Semicolon `;`, Tab (sends `\t`), Pipe `|`.
6. **F19** ignore plan/profile responses whose `source_id` no longer matches; stop the
   spinner on profile failure (show error); `error || "Run was cancelled"` style
   fallbacks; config fetch failure shows an error, not "No API key configured".
7. **F21** remove unused `planResult`/`runOptions` state; merge `ChangesTable` and
   `FlagsTable` into one generic paged table component.
8. **Tests:** add `vitest` (dev dependency, pinned) and reducer tests for: preview
   revision race, normalised plan handling, source switch ignoring stale responses.
   `npm run build` must still pass; add `npm test`.

Done when: `npm run build` and `npm test` pass; report lists each item → change.
