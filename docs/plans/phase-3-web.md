# Phase 3 — Web UI (React + TypeScript + Vite + React Spectrum v3)

**Source of truth:** `docs/SPEC.md` — read §10 (API, exact request/response shapes and
error bodies), §10.1, §11 (UI) fully, and skim §4/§5.3/§6 to understand the plan,
preview and report objects you render. Where this plan and the spec disagree, the spec
wins; note it in your final report.

## Ground rules
- Work only under `web/` (create it). Do not touch `backend/`, `docs/`, or anything else.
  No git commands that change state.
- Node: `/home/biswa/.local/node/bin` (node, npm, npx) — prepend it to `PATH` in every
  shell command (`export PATH=/home/biswa/.local/node/bin:$PATH`). Node is v26.
- The backend API may not exist yet while you work. Build against the spec's shapes; for
  manual checking you may add a tiny dev-only mock (e.g. a `?mock` flag or a
  `src/mock.ts` used only when `import.meta.env.VITE_MOCK === "1"`). Keep the mock out of
  the production code path.
- Dependencies: `react`, `react-dom` (18.x), `@adobe/react-spectrum` (v3),
  `@spectrum-icons/workflow` (icons), dev: `vite`, `@vitejs/plugin-react`, `typescript`,
  `@types/react`, `@types/react-dom`. Nothing else (no router, no state lib, no CSS
  framework). Pin exact versions in package.json (no `^`).

## Structure
```
web/
  package.json            scripts: dev, build ("tsc --noEmit && vite build"), typecheck, preview
  tsconfig.json           strict
  vite.config.ts          react plugin; dev server port 5173; proxy /api → http://127.0.0.1:8000
  index.html
  src/
    main.tsx              <Provider theme={defaultTheme}> (colorScheme follows OS) + <App/>
    App.tsx               layout: left step rail + content; owns the reducer
    state.ts              AppState, Action union, reducer (step, source, profile, rules,
                          planResult, plan (editable copy), previewStale, run)
    api.ts                typed fetch wrappers for every endpoint in §10; ApiError with
                          {status, detail, errors?}
    types.ts              TS types mirroring SourceInfo, ColumnInfo, Profile, Plan + op
                          union, Preview, RunStatus, RunReport, Change, Flag, Config
    opSummary.ts          one-line human summary per op type (e.g. regex_replace /[^0-9]/ → "")
    steps/Connect.tsx     Tabs CSV | SQL | MongoDB; CSV drop zone (DropZone + FileTrigger)
                          → POST /api/uploads; SQL/Mongo forms (URL/URI as password
                          TextField) → POST /api/sources; then ProfileTable
    steps/ProfileTable.tsx  TableView: column, type, empty %, distinct, top shapes;
                          non-cleanable rows get a "read-only" Badge
    steps/Plan.tsx        rules TextArea (one per line, # comments stripped before send),
                          Generate plan (disabled + ContextualHelp when !has_api_key),
                          indeterminate ProgressCircle while waiting, "Load plan JSON"
                          (FileTrigger → parse → POST /api/preview), data-sent notice
    steps/Review.tsx      planner notes + warnings (InlineAlert), LLM callout wording per
                          §5.3 ("up to ~N cells … (M unique in the sample of S)"), one
                          ColumnCard per planned column, actions bar (Re-run preview,
                          Download plan, Run cleaning)
    steps/ColumnCard.tsx  rationale; ordered ops list: Badge(op), summary, per-op
                          changed/flagged from preview, "no effect on sample" badge,
                          Switch (enabled), delete ActionButton; before→after examples;
                          flag examples; "Edit JSON" DialogTrigger with TextArea,
                          validated via POST /api/preview (show 422 `errors` in dialog)
    steps/RunDialog.tsx   switches: in-place (hidden for CSV / when !can_in_place) with an
                          AlertDialog confirm naming the target; skip LLM; overwrite
                          existing output; on 409 offer "overwrite and retry"
    steps/Results.tsx     polls GET /api/runs/{id} every 1 s while queued/running;
                          ProgressBar with phase label + Cancel; then summary tiles,
                          changes TableView (column Picker filter, before → after, sources,
                          "Load more"), flags TableView, download links for `files`;
                          failed/cancelled → error + rows_written for in-place
    components/ErrorBanner.tsx  shows ApiError detail + errors list
```

## Behaviour details
- Step rail: Connect → Plan → Review → Results. A step is enabled once its prerequisite
  exists (source → plan step; plan → review; run → results). Going back keeps state.
  404 on a source/run (server restarted) → reset to Connect with a message.
- Any plan edit (toggle, delete, JSON edit) sets `previewStale`; Review shows a banner and
  the Run button asks to re-run preview first (or runs anyway after confirm — choose the
  simpler: disable Run until preview re-run).
- Download plan: client-side Blob of the current plan JSON (`plan.json`).
- Download files: plain `<a href="/api/runs/{id}/files/{name}" download>`.
- All requests same-origin relative URLs (`/api/...`). Never send or display an API key.
- Accessibility: use Spectrum components' labels (`label`/`aria-label` on everything).
- Layout must work from ~1024px wide upward; no need for a phone layout beyond not
  breaking (use Flex wrap).

## Done when
- `npm install` and `npm run build` (tsc strict + vite build) succeed with zero type
  errors; `web/dist/index.html` exists.
- With the dev mock enabled you can click through all four steps (`npm run dev`), and
  you've checked it renders (describe what you verified; you can't use a browser, so at
  minimum `vite build` + a small render smoke test is acceptable — e.g. none required if
  build passes, but state it).
- Final report lists files, the exact package versions, any spec ambiguity and how you
  resolved it, and anything not done.
