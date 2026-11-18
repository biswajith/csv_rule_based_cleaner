import { useRef, useState } from "react";
import {
  Button, Content, Flex, Heading, InlineAlert, ProgressCircle, Text,
} from "@adobe/react-spectrum";
import { api } from "../api";
import { ApiError } from "../apiError";
import { ErrorBanner, RESTART_NOTICE, toApiError } from "../components/ErrorBanner";
import { ColumnCard } from "./ColumnCard";
import { RunButton } from "./RunDialog";
import type { Action, AppState } from "../state";
import type { ColumnPlan } from "../types";

export function Review({ state, dispatch }: { state: AppState; dispatch: (a: Action) => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const { plan, preview, source, previewStale } = state;
  const revisionRef = useRef(state.planRevision);
  revisionRef.current = state.planRevision;
  if (!plan || !preview || !source) return null;

  const hasLlmFix = plan.columns.some((c) => c.ops.some((o) => o.op === "llm_fix" && o.enabled !== false));

  function handle404(e: unknown): ApiError {
    const err = toApiError(e);
    if (err.status === 404) dispatch({ type: "reset", notice: RESTART_NOTICE });
    return err;
  }

  async function rerun() {
    setBusy(true);
    setError(null);
    const revision = state.planRevision;
    try {
      const r = await api.preview(source!.source_id, plan!);
      // The reducer drops this if the plan was edited (or the source changed) while it was in flight.
      dispatch({ type: "preview", sourceId: source!.source_id, revision, preview: r.preview, plan: r.plan });
    } catch (e) {
      setError(handle404(e));
    } finally {
      setBusy(false);
    }
  }

  async function editJson(index: number, column: ColumnPlan) {
    // Send the full plan with the column replaced; the server returns 422 + errors if invalid,
    // otherwise the normalised plan, which replaces ours.
    const revision = state.planRevision;
    const candidate = { ...plan!, columns: plan!.columns.map((c, i) => (i === index ? column : c)) };
    let r;
    try {
      r = await api.preview(source!.source_id, candidate);
    } catch (e) {
      throw handle404(e);
    }
    if (revisionRef.current !== revision) {
      throw new ApiError(0, "The plan changed while this edit was being validated. Please apply it again.");
    }
    dispatch({ type: "plan_replaced", sourceId: source!.source_id, revision, plan: r.plan, preview: r.preview });
  }

  function downloadPlan() {
    const blob = new Blob([JSON.stringify(plan, null, 2) + "\n"], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "plan.json";
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  }

  const maxCells = state.config?.max_llm_cells;
  const warnings = preview.warnings;

  return (
    <Flex direction="column" gap="size-200">
      <Heading level={2}>Review the plan</Heading>

      <Flex gap="size-150" alignItems="center" wrap>
        <Button variant="secondary" isPending={busy} onPress={rerun}>
          Re-run preview
        </Button>
        <Button variant="secondary" onPress={downloadPlan}>
          Download plan
        </Button>
        <RunButton source={source} plan={plan} hasLlmFix={hasLlmFix} isDisabled={previewStale || busy} dispatch={dispatch} />
        {busy && <ProgressCircle aria-label="Updating preview" isIndeterminate size="S" />}
        <Text UNSAFE_style={{ opacity: 0.7 }}>
          Preview covers {preview.sampled_rows} sampled rows
          {preview.total_rows != null ? ` of ${preview.total_rows}` : ""}.
        </Text>
      </Flex>
      <ErrorBanner error={error} title="Could not update the preview" />

      {previewStale && (
        <InlineAlert variant="notice">
          <Heading>Preview is out of date</Heading>
          <Content>You edited the plan. Re-run the preview before running the cleaning.</Content>
        </InlineAlert>
      )}

      {state.plan && state.plan.notes.length > 0 && (
        <InlineAlert variant="info">
          <Heading>Planner notes</Heading>
          <Content>
            <ul style={{ margin: 0, paddingLeft: 20 }}>
              {plan.notes.map((n, i) => (
                <li key={i}>{n}</li>
              ))}
            </ul>
          </Content>
        </InlineAlert>
      )}

      {warnings.length > 0 && (
        <InlineAlert variant="notice">
          <Heading>Warnings</Heading>
          <Content>
            <ul style={{ margin: 0, paddingLeft: 20 }}>
              {warnings.map((w, i) => (
                <li key={i}>{w}</li>
              ))}
            </ul>
          </Content>
        </InlineAlert>
      )}

      {(preview.llm_cells_sample > 0 || hasLlmFix) && (
        <InlineAlert variant="info">
          <Heading>LLM usage</Heading>
          <Content>
            {preview.llm_cells_estimate != null
              ? `Up to ~${preview.llm_cells_estimate} cells may go to the LLM`
              : `${preview.llm_cells_sample} cells in the sample may go to the LLM`}{" "}
            ({preview.llm_unique_sample} unique in the sample of {preview.sampled_rows})
            {maxCells != null ? `, capped at ${maxCells} unique cells per run` : ""}. Identical values are sent
            once.
          </Content>
        </InlineAlert>
      )}

      {state.usage && state.usage.calls > 0 && (
        <Text UNSAFE_style={{ opacity: 0.7 }}>
          Planning used {state.usage.calls} LLM call(s), {state.usage.input_tokens} input / {state.usage.output_tokens}{" "}
          output tokens.
        </Text>
      )}

      {plan.columns.length === 0 && <Text>The plan has no columns to clean.</Text>}
      {plan.columns.map((c, i) => (
        <ColumnCard
          key={`${i}:${c.column}`}
          index={i}
          column={c}
          preview={preview.columns.find((p) => p.column === c.column)}
          stale={previewStale}
          dispatch={dispatch}
          onEditJson={editJson}
        />
      ))}
    </Flex>
  );
}
