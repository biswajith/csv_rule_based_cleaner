# Phase 5 — Human approval of the plan

Owner decision (2026-11-17): a plan must be explicitly approved by a named person before
any data is cleaned. Plans start as drafts. Approval is recorded in the plan itself, and
any change to the plan after approval voids it.

Two agents work in parallel: **5A backend** (`backend/`, `docs/SPEC.md`, `README.md`) and
**5B web** (`web/` only). Both implement the shared contract exactly.

## Shared contract

A1. **Plan field.** `Plan` gains `approval: Approval | null` (default null) where
    `Approval = {by: string (1–100 chars after trimming), at: ISO-8601 UTC datetime,
    plan_hash: string}`.
A2. **Hash.** `plan_hash` = SHA-256 hex of
    `json.dumps(plan.model_dump(mode="json", exclude={"approval"}), sort_keys=True,
    separators=(",", ":"), ensure_ascii=False)`. A plan **is approved** iff
    `approval is not None and approval.plan_hash == hash(plan)`. Any edit (toggle,
    delete, JSON edit, rules change) changes the hash, so the approval no longer counts.
A3. **`POST /api/plans/approve`** body `{plan: object, by: string}` → 200
    `{plan}` (normalised plan with `approval` set; server sets `at` = now UTC and
    computes the hash). 422 for an invalid plan or blank `by`. Does not need a source.
A4. **`POST /api/preview`** returns the plan with `approval` kept only when it is still
    valid; a stale approval is replaced by `null` and the preview's `warnings` gets
    `"Approval by <by> was removed: the plan changed after it was approved."`. So in the
    UI, `plan.approval != null` ⇔ approved.
A5. **`POST /api/runs`** with a plan that is not approved → **409**
    `{detail: "Plan is not approved. Approve it before running."}` (or, for a stale
    approval, `"Plan changed after it was approved by <by>. Approve it again."`). Checked
    before opening the source.
A6. **`RunReport`** gains `approval: Approval | null` (copied from the plan). The run
    directory's `plan.json` already contains it.
A7. `POST /api/plan` (LLM planning) always returns a plan with `approval: null`.

## 5A — Backend

1. `plan.py`: `Approval` model; `Plan.approval`; `Plan.content_hash()`,
   `Plan.is_approved()`, `Plan.with_approval(by: str) -> Plan` (strip `by`, `ValueError`
   when blank/too long), and a helper `approval_error(plan) -> str | None` returning the
   A5 messages. `PlannerOutput` does not get the field.
2. Enforcement lives at the entry points (CLI `apply`/`run`, API `/api/runs`), **not** in
   `cleaner.clean` (library callers and the existing tests use unapproved plans). Document
   that in the spec.
3. API: A3, A4, A5, A6.
4. CLI:
   - `scrubber plan` writes a draft and tells the user to review and approve it
     (`scrubber approve plan.json`).
   - New `scrubber approve PLAN [SOURCE] [source options] [--by NAME] [--yes]`. Loads and
     validates the plan, prints a per-column summary of the ops. If SOURCE is given, also
     prints the dry-run preview on that source (reuse the `plan` command's preview
     display). Asks for the approver's name when `--by` is missing (default
     `getpass.getuser()`), then `Approve this plan as <name>? [y/N]` unless `--yes`.
     Rewrites the plan file with the approval and prints who/when. Approving an already
     approved, unchanged plan replaces the approval (new name/time).
   - `scrubber apply` refuses an unapproved plan (exit 2) with the A5 message plus
     ``Run `scrubber approve <file>`.``
   - `scrubber run`: after the preview, the "Proceed?" prompt becomes approval: ask for the
     approver's name (default `getpass.getuser()`), then `Approve this plan as <name> and
     apply? [y/N]`. With `--yes`, `--approved-by NAME` is required (error otherwise) —
     `--yes` alone never approves. Also add `--save-plan PATH` to write the approved plan.
   - The in-place "type the table name" confirmation stays as it is, in addition.
5. Tests (new file `tests/test_approval.py`): hash stability (dump → load → same hash;
   key order irrelevant); edit voids approval; `with_approval` blank name; API approve
   endpoint; preview drops a stale approval with the warning; runs 409 for none/stale and
   success when approved; report carries approval; CLI approve (with `--by --yes`, with
   SOURCE preview), apply refusal and success, run with `--yes` without `--approved-by`
   fails, run with `--yes --approved-by` succeeds. Update existing API/CLI tests that
   start runs so they approve their plans first.
6. Docs: `docs/SPEC.md` — new §4.5 "Approval", update §9 CLI, §10 API, §11 UI (Review
   step approval panel; Run disabled until approved), add an owner-decision line.
   `README.md` — the flow becomes Profile → Plan → Review & approve → Clean; CLI examples
   include `scrubber approve`.

Done when all backend tests pass and the report lists each item → change → test names.

## 5B — Web

1. `types.ts`: `Approval`; `Plan.approval?: Approval | null`; `RunReport.approval`.
2. `api.ts`: `approvePlan(plan, by) → {plan}`.
3. `state.ts`:
   - New action `plan_approved {sourceId, revision, plan}` — ignored when stale (same
     rules as `plan_replaced`), otherwise stores the plan (revision unchanged; the
     preview stays valid because only `approval` changed).
   - Every edit action (`toggle_op`, `delete_op`, `plan_replaced` from Edit JSON, and any
     other plan mutation) sets `plan.approval = null` in the stored plan.
   - `planned` (new LLM plan) stores whatever the server returned (null approval).
4. Review step: an **Approval** panel above the Run button.
   - Approved: positive status "Approved by <by> on <local date/time>".
   - Draft: notice "Draft — a person must approve this plan before it can run.", a
     "Your name" TextField (remember the last name in `localStorage`, wrapped in
     try/catch), and an "Approve plan" button (disabled while the name is blank or a
     preview is pending). Calls `approvePlan`, dispatches `plan_approved`.
   - The Run button is disabled until approved, with help text saying why.
   - A 409 from `/api/runs` shows in the run dialog's error banner (no reset).
5. Results summary shows "Plan approved by <by> on <date>" when `report.approval` is set.
6. `mock.ts` handles `/api/plans/approve` (sets `approval` with a fake hash).
7. Tests (`state.test.ts`): edits clear approval; `plan_approved` applies; stale
   `plan_approved` ignored; `planned` resets approval; loading a plan whose preview
   response has approval keeps it.

Done when `npm test` and `npm run build` pass; report lists each item → change.
