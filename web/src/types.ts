// Types mirroring docs/SPEC.md §4, §5, §6, §10.

export interface Config {
  model: string;
  planner_model: string;
  has_api_key: boolean;
  sample_size: number;
  max_llm_cells: number;
  max_upload_mb: number;
}

export interface ColumnInfo {
  name: string;
  cleanable: boolean;
  nullable: boolean;
  max_length: number | null;
}

export interface SourceInfo {
  source_id: string;
  kind: "csv" | "sql" | "mongo";
  description: string;
  columns: ColumnInfo[];
  count: number | null;
  can_in_place: boolean;
}

export interface ShapeStat {
  shape: string;
  count: number;
  example: string;
}

export interface ColumnProfile {
  name: string;
  cleanable: boolean;
  inferred_type: string;
  non_empty: number;
  empty: number;
  distinct_count: number;
  top_values: [string, number][];
  shapes: ShapeStat[];
  rare_examples: string[];
  all_values: string[] | null;
}

export interface Profile {
  source: string;
  total_rows: number | null;
  sampled_rows: number;
  columns: ColumnProfile[];
}

// ---- Plan ----

interface OpBase {
  enabled?: boolean;
  note?: string;
}
export type Op =
  | ({ op: "trim" } & OpBase)
  | ({ op: "collapse_whitespace" } & OpBase)
  | ({ op: "case"; mode: "lower" | "upper" | "title" } & OpBase)
  | ({ op: "regex_replace"; pattern: string; replacement?: string; ignore_case?: boolean } & OpBase)
  | ({ op: "reformat"; pattern: string; template: string; ignore_case?: boolean } & OpBase)
  | ({ op: "translate"; mapping: Record<string, string>; ignore_case?: boolean } & OpBase)
  | ({ op: "map_values"; mapping: Record<string, string>; ignore_case?: boolean } & OpBase)
  | ({
      op: "map_from_column";
      source_column: string;
      mapping: Record<string, string>;
      ignore_case?: boolean;
      mode?: "overwrite" | "fill_empty";
    } & OpBase)
  | ({ op: "null_if"; values?: string[]; pattern?: string | null } & OpBase)
  | ({ op: "parse_date"; output_format?: string; day_first?: boolean } & OpBase)
  | ({
      op: "validate";
      pattern: string;
      message: string;
      ignore_case?: boolean;
      allow_empty?: boolean;
    } & OpBase)
  | ({
      op: "llm_fix";
      instruction: string;
      when?: "if_flagged" | "if_not_match" | "always";
      pattern?: string | null;
      context_columns?: string[];
    } & OpBase);

export interface ColumnPlan {
  column: string;
  rationale?: string;
  ops: Op[];
}

export interface Approval {
  by: string;
  at: string;
  plan_hash: string;
}

export interface Plan {
  version: number;
  rules: string[];
  columns: ColumnPlan[];
  notes: string[];
  /** Set only when a person approved exactly this plan; any edit clears it. */
  approval?: Approval | null;
}

// ---- Preview ----

export interface OpStat {
  op_index: number;
  op: string;
  changed: number;
  flagged: number;
}

export interface ColumnPreview {
  column: string;
  cells_changed: number;
  cells_flagged: number;
  ops: OpStat[];
  examples: [string, string][];
  flag_examples: [string, string][];
  llm_cells: number;
  llm_unique: number;
}

export interface Preview {
  sampled_rows: number;
  total_rows: number | null;
  columns: ColumnPreview[];
  llm_cells_sample: number;
  llm_unique_sample: number;
  llm_cells_estimate: number | null;
  warnings: string[];
}

export interface Usage {
  calls: number;
  failed_calls: number;
  input_tokens: number;
  output_tokens: number;
}

/** POST /api/preview response: the server-normalised plan plus its preview. */
export interface PreviewResult {
  plan: Plan;
  preview: Preview;
}

export interface PlanResult {
  plan: Plan;
  preview: Preview;
  usage: Usage;
}

// ---- Runs ----

export type RunState = "queued" | "running" | "done" | "failed" | "cancelled";
export type Phase = "scanning" | "resolving" | "writing";

export interface RunReport {
  approval?: Approval | null;
  run_id: string;
  status: "done" | "failed" | "cancelled";
  error: string | null;
  source: string;
  target: string;
  in_place: boolean;
  started_at: string;
  finished_at: string;
  duration_s: number;
  rows: number;
  rows_written: number;
  cells_changed: number;
  cells_flagged: number;
  columns: { column: string; cells_changed: number; cells_flagged: number; ops: OpStat[] }[];
  flags_written: number;
  llm: {
    model: string | null;
    calls: number;
    failed_calls: number;
    input_tokens: number;
    output_tokens: number;
    cells_triggered: number;
    cells_unique: number;
    cells_sent: number;
    cells_fixed: number;
    cells_over_budget: number;
  };
}

export interface RunStatus {
  run_id: string;
  status: RunState;
  phase: Phase | null;
  done: number;
  total: number | null;
  error: string | null;
  report?: RunReport | null;
  files: string[];
}

export interface Change {
  row_key: string | number;
  column: string;
  before: string;
  after: string;
  sources: string[];
}

export interface Flag {
  row_key: string | number;
  column: string;
  value: string;
  message: string;
}

export interface Page<T> {
  total: number;
  items: T[];
}

export interface RunOptions {
  in_place: boolean;
  overwrite: boolean;
  no_llm: boolean;
}
