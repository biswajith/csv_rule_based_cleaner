import type { ColumnPlan, Config, Plan, PlanResult, Preview, Profile, SourceInfo, Usage } from "./types";

export type StepId = "connect" | "plan" | "review" | "results";

export interface AppState {
  step: StepId;
  config: Config | null;
  configError: string | null;
  source: SourceInfo | null;
  profile: Profile | null;
  profileError: string | null;
  rulesText: string;
  plan: Plan | null; // editable copy of the server-normalised plan
  /** Bumped by every plan edit / replacement; never reset, so stale responses cannot match. */
  planRevision: number;
  preview: Preview | null;
  usage: Usage | null;
  previewStale: boolean;
  runId: string | null;
  notice: string | null; // e.g. "server restarted"
}

export const initialState: AppState = {
  step: "connect",
  config: null,
  configError: null,
  source: null,
  profile: null,
  profileError: null,
  rulesText: "",
  plan: null,
  planRevision: 0,
  preview: null,
  usage: null,
  previewStale: false,
  runId: null,
  notice: null,
};

export type Action =
  | { type: "config"; config: Config }
  | { type: "config_error"; error: string }
  | { type: "goto"; step: StepId }
  | { type: "source"; source: SourceInfo }
  | { type: "profile"; sourceId: string; profile: Profile }
  | { type: "profile_failed"; sourceId: string; error: string }
  | { type: "rules"; text: string }
  | { type: "planned"; sourceId: string; result: PlanResult }
  | { type: "plan_loaded"; sourceId: string; plan: Plan; preview: Preview }
  /** A preview computed for plan revision `revision`; `plan` is the server-normalised plan. */
  | { type: "preview"; sourceId: string; revision: number; preview: Preview; plan?: Plan }
  /** Edit JSON: the server accepted a whole plan (with a column replaced) built from `revision`. */
  | { type: "plan_replaced"; sourceId: string; revision: number; plan: Plan; preview: Preview }
  /** The server recorded an approval on the plan built from `revision`. */
  | { type: "plan_approved"; sourceId: string; revision: number; plan: Plan }
  | { type: "toggle_op"; col: number; op: number; enabled: boolean }
  | { type: "delete_op"; col: number; op: number }
  | { type: "run_started"; runId: string }
  | { type: "reset"; notice: string | null };

function withColumn(plan: Plan, index: number, f: (c: ColumnPlan) => ColumnPlan): Plan {
  return { ...plan, columns: plan.columns.map((c, i) => (i === index ? f(c) : c)) };
}

/** Apply a local plan edit: new plan, new revision, preview marked stale. */
function edited(state: AppState, plan: Plan): AppState {
  return { ...state, plan: { ...plan, approval: null }, planRevision: state.planRevision + 1, previewStale: true };
}

const isCurrent = (state: AppState, sourceId: string) => state.source?.source_id === sourceId;

export function reducer(state: AppState, a: Action): AppState {
  switch (a.type) {
    case "config":
      return { ...state, config: a.config, configError: null };
    case "config_error":
      return { ...state, configError: a.error };
    case "goto":
      return { ...state, step: a.step };
    case "source":
      // A new source invalidates everything derived from the old one.
      return {
        ...initialState,
        config: state.config,
        configError: state.configError,
        rulesText: state.rulesText,
        planRevision: state.planRevision + 1,
        source: a.source,
      };
    case "profile":
      if (!isCurrent(state, a.sourceId)) return state;
      return { ...state, profile: a.profile, profileError: null };
    case "profile_failed":
      if (!isCurrent(state, a.sourceId)) return state;
      return { ...state, profileError: a.error };
    case "rules":
      return { ...state, rulesText: a.text };
    case "planned":
      if (!isCurrent(state, a.sourceId)) return state;
      return {
        ...state,
        plan: structuredClone(a.result.plan),
        planRevision: state.planRevision + 1,
        preview: a.result.preview,
        usage: a.result.usage,
        previewStale: false,
        step: "review",
      };
    case "plan_loaded":
      if (!isCurrent(state, a.sourceId)) return state;
      return {
        ...state,
        plan: structuredClone(a.plan),
        planRevision: state.planRevision + 1,
        preview: a.preview,
        usage: null,
        previewStale: false,
        step: "review",
      };
    case "preview":
      if (!isCurrent(state, a.sourceId) || !state.plan || a.revision !== state.planRevision) return state;
      return {
        ...state,
        preview: a.preview,
        plan: a.plan ? structuredClone(a.plan) : state.plan,
        previewStale: false,
      };
    case "plan_replaced":
      if (!isCurrent(state, a.sourceId) || !state.plan || a.revision !== state.planRevision) return state;
      return {
        ...state,
        plan: a.plan.approval ? { ...structuredClone(a.plan), approval: null } : structuredClone(a.plan), // an edit voids any approval
        planRevision: state.planRevision + 1,
        preview: a.preview,
        previewStale: false,
      };
    case "plan_approved":
      if (!isCurrent(state, a.sourceId) || !state.plan || a.revision !== state.planRevision) return state;
      return { ...state, plan: structuredClone(a.plan) };
    case "toggle_op":
      if (!state.plan) return state;
      return edited(
        state,
        withColumn(state.plan, a.col, (c) => ({
          ...c,
          ops: c.ops.map((o, i) => (i === a.op ? { ...o, enabled: a.enabled } : o)),
        })),
      );
    case "delete_op":
      if (!state.plan) return state;
      return edited(state, withColumn(state.plan, a.col, (c) => ({ ...c, ops: c.ops.filter((_, i) => i !== a.op) })));
    case "run_started":
      return { ...state, runId: a.runId, step: "results" };
    case "reset":
      return {
        ...initialState,
        config: state.config,
        configError: state.configError,
        planRevision: state.planRevision + 1,
        notice: a.notice,
      };
  }
}
