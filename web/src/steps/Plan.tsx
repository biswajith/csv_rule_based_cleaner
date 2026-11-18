import { useState } from "react";
import {
  Button, Content, ContextualHelp, FileTrigger, Flex, Heading, InlineAlert, ProgressCircle, Text,
  TextArea,
} from "@adobe/react-spectrum";
import { api } from "../api";
import { ApiError } from "../apiError";
import { ErrorBanner, RESTART_NOTICE, toApiError } from "../components/ErrorBanner";
import type { Action, AppState } from "../state";
import type { Plan as PlanT } from "../types";

/** One rule per line; blank lines and `#` comment lines are dropped. */
export function parseRules(text: string): string[] {
  return text
    .split(/\r?\n/)
    .map((l) => l.trim())
    .filter((l) => l !== "" && !l.startsWith("#"));
}

export function PlanStep({ state, dispatch }: { state: AppState; dispatch: (a: Action) => void }) {
  const [busy, setBusy] = useState<"plan" | "load" | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const source = state.source!;
  const hasKey = state.config?.has_api_key ?? false;
  const configFailed = !state.config && state.configError !== null;
  const rules = parseRules(state.rulesText);

  function fail(e: unknown) {
    const err = toApiError(e);
    if (err.status === 404) dispatch({ type: "reset", notice: RESTART_NOTICE });
    else setError(err);
  }

  async function generate() {
    setBusy("plan");
    setError(null);
    try {
      const result = await api.plan(source.source_id, rules);
      dispatch({ type: "planned", sourceId: source.source_id, result });
    } catch (e) {
      fail(e);
    } finally {
      setBusy(null);
    }
  }

  async function loadPlan(files: FileList | null) {
    const f = files?.[0];
    if (!f) return;
    setBusy("load");
    setError(null);
    try {
      let raw: PlanT;
      try {
        raw = JSON.parse(await f.text()) as PlanT;
      } catch {
        throw new ApiError(0, `${f.name} is not valid JSON.`);
      }
      // Keep the server-normalised plan, never the raw uploaded JSON.
      const { plan, preview } = await api.preview(source.source_id, raw);
      dispatch({ type: "plan_loaded", sourceId: source.source_id, plan, preview });
    } catch (e) {
      fail(e);
    } finally {
      setBusy(null);
    }
  }

  return (
    <Flex direction="column" gap="size-200" maxWidth="size-6000">
      <Heading level={2}>Describe the cleaning rules</Heading>
      <TextArea
        label="Rules"
        description="One rule per line. Lines starting with # are ignored."
        value={state.rulesText}
        onChange={(t) => dispatch({ type: "rules", text: t })}
        width="100%"
        height="size-2400"
        isDisabled={busy !== null}
      />
      <InlineAlert variant="info">
        <Heading>Data sent to the model provider</Heading>
        <Content>
          Generating a plan sends your rule text and sampled values from the profile to the model provider
          {state.config ? ` (${state.config.planner_model})` : ""}. Fixing cells with an LLM step later sends only the
          triggered cell values and their context columns{state.config ? ` (${state.config.model})` : ""}. Loading a plan JSON file sends nothing.
        </Content>
      </InlineAlert>
      <Flex gap="size-150" alignItems="center" wrap>
        <Button variant="accent" isDisabled={!hasKey || rules.length === 0 || busy !== null} onPress={generate}>
          Generate plan
        </Button>
        {state.config && !hasKey && (
          <ContextualHelp variant="info">
            <Heading>No API key configured</Heading>
            <Content>
              <Text>
                Set OPENAI_API_KEY in the server environment (or its .env file) and restart the server to
                generate plans. You can still load a plan JSON file.
              </Text>
            </Content>
          </ContextualHelp>
        )}
        <FileTrigger acceptedFileTypes={[".json", "application/json"]} onSelect={loadPlan}>
          <Button variant="secondary" isDisabled={busy !== null}>
            Load plan JSON
          </Button>
        </FileTrigger>
        {busy && (
          <Flex gap="size-100" alignItems="center">
            <ProgressCircle aria-label={busy === "plan" ? "Generating plan" : "Loading plan"} isIndeterminate size="S" />
            <Text>{busy === "plan" ? "Generating plan — this can take a minute or two…" : "Checking plan…"}</Text>
          </Flex>
        )}
      </Flex>
      {configFailed && (
        <ErrorBanner
          error={new ApiError(0, `Could not load the server configuration: ${state.configError}. Reload the page to retry.`)}
          title="Server configuration unavailable"
        />
      )}
      <ErrorBanner error={error} title="Could not create the plan" />
    </Flex>
  );
}
