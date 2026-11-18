import { describe, expect, it } from "vitest";
import { initialState, reducer } from "./state";
import type { Action, AppState } from "./state";
import { hasNoEffect } from "./noEffect";
import type { ColumnPreview, Plan, Preview, Profile, SourceInfo } from "./types";

const src = (id: string): SourceInfo => ({
  source_id: id, kind: "csv", description: id, columns: [], count: 1, can_in_place: false,
});
const mkPlan = (extra: Partial<Plan> = {}): Plan => ({
  version: 1, rules: [], notes: [],
  columns: [{ column: "a", ops: [{ op: "trim" }, { op: "case", mode: "lower" }] }],
  ...extra,
});
const mkPreview = (tag: number): Preview => ({
  sampled_rows: tag, total_rows: null, columns: [], llm_cells_sample: 0, llm_unique_sample: 0,
  llm_cells_estimate: null, warnings: [],
});
const profile: Profile = { source: "s", total_rows: 1, sampled_rows: 1, columns: [] };

function run(actions: Action[], from: AppState = initialState): AppState {
  return actions.reduce(reducer, from);
}
const withPlan = () =>
  run([{ type: "source", source: src("s1") }, { type: "planned", sourceId: "s1", result: { plan: mkPlan(), preview: mkPreview(1), usage: { calls: 1, failed_calls: 0, input_tokens: 0, output_tokens: 0 } } }]);

describe("preview revision race", () => {
  it("drops a preview requested before a later edit", () => {
    const s0 = withPlan();
    const requested = s0.planRevision;
    const s1 = run([{ type: "toggle_op", col: 0, op: 0, enabled: false }], s0);
    expect(s1.planRevision).toBeGreaterThan(requested);
    const s2 = reducer(s1, { type: "preview", sourceId: "s1", revision: requested, preview: mkPreview(99) });
    expect(s2).toBe(s1);
    expect(s2.previewStale).toBe(true);
    expect(s2.preview?.sampled_rows).toBe(1);
  });

  it("applies a preview for the current revision and clears staleness", () => {
    const s1 = run([{ type: "delete_op", col: 0, op: 0 }], withPlan());
    expect(s1.previewStale).toBe(true);
    const s2 = reducer(s1, { type: "preview", sourceId: "s1", revision: s1.planRevision, preview: mkPreview(5) });
    expect(s2.previewStale).toBe(false);
    expect(s2.preview?.sampled_rows).toBe(5);
  });

  it("every kind of edit bumps the revision", () => {
    const s0 = withPlan();
    const s1 = reducer(s0, { type: "toggle_op", col: 0, op: 0, enabled: false });
    const s2 = reducer(s1, { type: "delete_op", col: 0, op: 0 });
    expect(new Set([s0.planRevision, s1.planRevision, s2.planRevision]).size).toBe(3);
  });

  it("drops an Edit JSON replacement computed from an outdated revision", () => {
    const s0 = withPlan();
    const s1 = reducer(s0, { type: "toggle_op", col: 0, op: 1, enabled: false });
    const s2 = reducer(s1, { type: "plan_replaced", sourceId: "s1", revision: s0.planRevision, plan: mkPlan({ notes: ["x"] }), preview: mkPreview(7) });
    expect(s2).toBe(s1);
  });
});

describe("normalised plan handling", () => {
  it("plan_loaded stores the plan it is given (server-normalised) and a deep copy", () => {
    const normalised = mkPlan({ notes: ["defaults filled"] });
    const s = run([{ type: "source", source: src("s1") }, { type: "plan_loaded", sourceId: "s1", plan: normalised, preview: mkPreview(2) }]);
    expect(s.plan).toEqual(normalised);
    expect(s.plan).not.toBe(normalised);
    expect(s.step).toBe("review");
    expect(s.previewStale).toBe(false);
  });

  it("a current preview carrying a normalised plan replaces the local plan without a new revision", () => {
    const s0 = withPlan();
    const normalised = mkPlan({ notes: ["n"] });
    const s1 = reducer(s0, { type: "preview", sourceId: "s1", revision: s0.planRevision, preview: mkPreview(3), plan: normalised });
    expect(s1.plan).toEqual(normalised);
    expect(s1.planRevision).toBe(s0.planRevision);
  });

  it("plan_replaced adopts the returned plan and bumps the revision", () => {
    const s0 = withPlan();
    const returned = mkPlan({ notes: ["from server"] });
    const s1 = reducer(s0, { type: "plan_replaced", sourceId: "s1", revision: s0.planRevision, plan: returned, preview: mkPreview(4) });
    expect(s1.plan).toEqual(returned);
    expect(s1.planRevision).toBe(s0.planRevision + 1);
    expect(s1.previewStale).toBe(false);
  });
});

describe("source switch ignores stale responses", () => {
  it("drops profile, plan and preview responses for the previous source", () => {
    const s0 = withPlan();
    const rev = s0.planRevision;
    const s1 = reducer(s0, { type: "source", source: src("s2") });
    expect(s1.plan).toBeNull();
    expect(s1.planRevision).toBeGreaterThan(rev);

    expect(reducer(s1, { type: "profile", sourceId: "s1", profile })).toBe(s1);
    expect(reducer(s1, { type: "profile_failed", sourceId: "s1", error: "x" })).toBe(s1);
    expect(reducer(s1, { type: "planned", sourceId: "s1", result: { plan: mkPlan(), preview: mkPreview(1), usage: { calls: 0, failed_calls: 0, input_tokens: 0, output_tokens: 0 } } })).toBe(s1);
    expect(reducer(s1, { type: "plan_loaded", sourceId: "s1", plan: mkPlan(), preview: mkPreview(1) })).toBe(s1);
    expect(reducer(s1, { type: "preview", sourceId: "s1", revision: rev, preview: mkPreview(1) })).toBe(s1);
  });

  it("accepts responses for the current source and records profile failures", () => {
    const s1 = run([{ type: "source", source: src("s2") }]);
    expect(reducer(s1, { type: "profile", sourceId: "s2", profile }).profile).toBe(profile);
    expect(reducer(s1, { type: "profile_failed", sourceId: "s2", error: "boom" }).profileError).toBe("boom");
  });
});

describe("no-effect badge rule", () => {
  const col = (llm_cells: number): ColumnPreview => ({
    column: "a", cells_changed: 0, cells_flagged: 0, ops: [], examples: [], flag_examples: [], llm_cells, llm_unique: 0,
  });
  const zero = { op_index: 0, op: "x", changed: 0, flagged: 0 };
  it("warns for the listed ops only when nothing changed or flagged", () => {
    expect(hasNoEffect({ op: "map_values", mapping: {} }, zero, col(0))).toBe(true);
    expect(hasNoEffect({ op: "map_values", mapping: {} }, { ...zero, changed: 1 }, col(0))).toBe(false);
    expect(hasNoEffect({ op: "parse_date" }, { ...zero, flagged: 2 }, col(0))).toBe(false);
    expect(hasNoEffect({ op: "map_values", mapping: {}, enabled: false }, zero, col(0))).toBe(false);
  });
  it("never warns for trim, collapse_whitespace, case, validate", () => {
    expect(hasNoEffect({ op: "trim" }, zero, col(0))).toBe(false);
    expect(hasNoEffect({ op: "collapse_whitespace" }, zero, col(0))).toBe(false);
    expect(hasNoEffect({ op: "case", mode: "lower" }, zero, col(0))).toBe(false);
    expect(hasNoEffect({ op: "validate", pattern: ".", message: "m" }, zero, col(0))).toBe(false);
  });
  it("warns for llm_fix only when the column sent no cells to the LLM", () => {
    expect(hasNoEffect({ op: "llm_fix", instruction: "i" }, zero, col(0))).toBe(true);
    expect(hasNoEffect({ op: "llm_fix", instruction: "i" }, zero, col(3))).toBe(false);
  });
});
