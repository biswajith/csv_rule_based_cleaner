// Dev-only mock backend, used when built/served with VITE_MOCK=1 (npm run dev:mock).
import { ApiError } from "./apiError";
import type {
  Change, Config, Flag, Page, Plan, PlanResult, Preview, PreviewResult, Profile, RunOptions, RunReport, RunStatus, SourceInfo,
} from "./types";

const delay = (ms = 400) => new Promise((r) => setTimeout(r, ms));

const cols = ["name", "phone", "email", "state", "id"];
const source: SourceInfo = {
  source_id: "mock-source",
  kind: "csv",
  description: "CSV people.csv",
  columns: cols.map((name) => ({ name, cleanable: name !== "id", nullable: true, max_length: null })),
  count: 12000,
  can_in_place: false,
};

const profile: Profile = {
  source: source.description,
  total_rows: 12000,
  sampled_rows: 1000,
  columns: cols.map((name, i) => ({
    name,
    cleanable: name !== "id",
    inferred_type: name === "id" ? "int" : "string",
    non_empty: 950,
    empty: 50,
    distinct_count: 100 + i * 200,
    top_values: [["x", 5]],
    shapes: [
      { shape: name === "phone" ? "(999) 999-9999" : "A", count: 600, example: "x" },
      { shape: name === "phone" ? "999-999-9999" : "A A", count: 300, example: "y" },
    ],
    rare_examples: [],
    all_values: null,
  })),
};

const plan: Plan = {
  version: 1,
  rules: ["Phone numbers should be (XXX) XXX-XXXX", "Emails lowercase"],
  columns: [
    {
      column: "phone",
      rationale: "Mixed separators; normalize to (XXX) XXX-XXXX",
      ops: [
        { op: "regex_replace", pattern: "[^0-9]", replacement: "" },
        { op: "reformat", pattern: "1?(\\d{3})(\\d{3})(\\d{4})", template: "(\\1) \\2-\\3" },
        { op: "validate", pattern: "\\(\\d{3}\\) \\d{3}-\\d{4}", message: "Not a 10-digit US number" },
      ],
    },
    {
      column: "email",
      ops: [
        { op: "trim" },
        { op: "case", mode: "lower" },
        { op: "validate", pattern: "[^@\\s]+@[^@\\s]+\\.[a-z]{2,}", message: "Invalid email" },
        { op: "llm_fix", when: "if_flagged", instruction: "Repair mistyped email addresses", context_columns: ["name"] },
      ],
    },
  ],
  notes: ["Rule 'remove duplicates' not applied: records are always preserved"],
};

function makePreview(p: Plan): Preview {
  return {
    sampled_rows: 1000,
    total_rows: 12000,
    columns: p.columns.map((c) => ({
      column: c.column,
      cells_changed: 420,
      cells_flagged: 12,
      ops: c.ops.map((o, i) => ({ op_index: i, op: o.op, changed: o.op === "validate" ? 0 : 300 - i * 50, flagged: o.op === "validate" ? 12 : 0 })),
      examples: [["555-123-4567", "(555) 123-4567"], ["", ""]],
      flag_examples: [["12345", "Not a 10-digit US number"]],
      llm_cells: c.ops.some((o) => o.op === "llm_fix") ? 12 : 0,
      llm_unique: c.ops.some((o) => o.op === "llm_fix") ? 9 : 0,
    })),
    llm_cells_sample: 12,
    llm_unique_sample: 9,
    llm_cells_estimate: 144,
    warnings: ["email: validate on sample changed nothing (example warning)"],
  };
}

let runStart = 0;
let cancelled = false;
const N_CHANGES = 250;

function report(status: "done" | "cancelled"): RunReport {
  return {
    run_id: "20260101-000000-abcd",
    status,
    error: null,
    source: source.description,
    target: "output/20260101-000000-abcd/people.cleaned.csv",
    in_place: false,
    started_at: "",
    finished_at: "",
    duration_s: 6.2,
    rows: 12000,
    rows_written: 12000,
    cells_changed: N_CHANGES,
    cells_flagged: 130,
    flags_written: 130,
    columns: [
      { column: "phone", cells_changed: 200, cells_flagged: 100, ops: [] },
      { column: "email", cells_changed: 50, cells_flagged: 30, ops: [] },
    ],
    llm: { model: "mock", calls: 4, failed_calls: 0, input_tokens: 4200, output_tokens: 900, cells_triggered: 144, cells_unique: 90, cells_sent: 90, cells_fixed: 70, cells_over_budget: 0 },
  };
}

export const mock = {
  config: async (): Promise<Config> => ({ model: "openai:mock", has_api_key: true, sample_size: 1000, max_llm_cells: 5000, max_upload_mb: 200 }),
  upload: async (_f: File, _d: string, _e: string): Promise<SourceInfo> => (await delay(), source),
  addSource: async (_s: Record<string, unknown>): Promise<SourceInfo> => (await delay(), { ...source, kind: "sql", can_in_place: true, description: "sqlite:///mock.db table people" }),
  profile: async (_id: string): Promise<Profile> => (await delay(200), profile),
  plan: async (_id: string, rules: string[]): Promise<PlanResult> => {
    await delay(1500);
    if (rules.some((r) => /fail/i.test(r))) throw new ApiError(422, "Plan invalid", ["column 'foo' not found; available: name, phone"]);
    return { plan, preview: makePreview(plan), usage: { calls: 1, failed_calls: 0, input_tokens: 3000, output_tokens: 800 } };
  },
  preview: async (_id: string, p: Plan): Promise<PreviewResult> => {
    await delay(300);
    if (!p || !Array.isArray(p.columns)) throw new ApiError(422, "Invalid plan", ["columns: field required"]);
    return { plan: p, preview: makePreview(p) };
  },
  startRun: async (_id: string, _p: Plan, _o: RunOptions): Promise<{ run_id: string }> => {
    await delay(200);
    runStart = Date.now();
    cancelled = false;
    return { run_id: "20260101-000000-abcd" };
  },
  run: async (id: string): Promise<RunStatus> => {
    await delay(100);
    const t = (Date.now() - runStart) / 1000;
    if (cancelled) return { run_id: id, status: "cancelled", phase: null, done: 0, total: null, error: "Cancelled", report: report("cancelled"), files: [] };
    if (t < 1) return { run_id: id, status: "queued", phase: null, done: 0, total: null, error: null, files: [] };
    if (t < 6) return { run_id: id, status: "running", phase: t < 3 ? "scanning" : t < 4 ? "resolving" : "writing", done: Math.round(t * 2000), total: 12000, error: null, files: [] };
    return { run_id: id, status: "done", phase: null, done: 12000, total: 12000, error: null, report: report("done"), files: ["people.cleaned.csv", "changes.csv", "flags.csv", "plan.json", "report.json"] };
  },
  cancel: async (id: string): Promise<RunStatus> => {
    cancelled = true;
    return { run_id: id, status: "cancelled", phase: null, done: 0, total: null, error: null, files: [] };
  },
  changes: async (_id: string, column: string, offset: number, limit: number): Promise<Page<Change>> => {
    await delay(150);
    const all: Change[] = Array.from({ length: N_CHANGES }, (_, i) => ({
      row_key: i, column: i % 4 ? "phone" : "email", before: "555-123-" + String(1000 + i), after: "(555) 123-" + String(1000 + i), sources: ["regex_replace", "reformat"],
    })).filter((c) => !column || c.column === column);
    return { total: all.length, items: all.slice(offset, offset + limit) };
  },
  flags: async (_id: string, column: string, offset: number, limit: number): Promise<Page<Flag>> => {
    await delay(150);
    const all: Flag[] = Array.from({ length: 30 }, (_, i) => ({ row_key: i * 7, column: i % 2 ? "phone" : "email", value: "bad" + i, message: "Invalid value" })).filter((f) => !column || f.column === column);
    return { total: all.length, items: all.slice(offset, offset + limit) };
  },
};
