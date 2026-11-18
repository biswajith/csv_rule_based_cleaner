import { useEffect, useReducer } from "react";
import { Divider, Flex, Heading, ToggleButton, View, Text } from "@adobe/react-spectrum";
import { api } from "./api";
import { initialState, reducer } from "./state";
import type { StepId } from "./state";
import { Connect } from "./steps/Connect";
import { PlanStep } from "./steps/Plan";
import { Review } from "./steps/Review";
import { Results } from "./steps/Results";

const STEPS: { id: StepId; label: string }[] = [
  { id: "connect", label: "1. Connect" },
  { id: "plan", label: "2. Plan" },
  { id: "review", label: "3. Review" },
  { id: "results", label: "4. Results" },
];

export function App() {
  const [state, dispatch] = useReducer(reducer, initialState);

  useEffect(() => {
    api
      .config()
      .then((config) => dispatch({ type: "config", config }))
      .catch((e) => dispatch({ type: "config_error", error: e instanceof Error ? e.message : String(e) }));
  }, []);

  const enabled: Record<StepId, boolean> = {
    connect: true,
    plan: !!state.source,
    review: !!state.plan,
    results: !!state.runId,
  };

  return (
    <Flex height="100%" UNSAFE_style={{ overflow: "hidden" }}>
      <View
        width="size-2400"
        flexShrink={0}
        padding="size-200"
        UNSAFE_style={{ borderRight: "1px solid var(--spectrum-global-color-gray-300)" }}
      >
        <Flex direction="column" gap="size-100" aria-label="Steps" UNSAFE_className="step-rail">
          <Heading level={1} margin={0}>
            Scrubber
          </Heading>
          <Text UNSAFE_style={{ opacity: 0.7 }}>Plan first, then clean.</Text>
          <Divider size="S" marginY="size-100" />
          <nav aria-label="Steps">
            <Flex direction="column" gap="size-75">
              {STEPS.map((s) => (
                <ToggleButton
                  key={s.id}
                  isQuiet
                  isSelected={state.step === s.id}
                  isDisabled={!enabled[s.id]}
                  onPress={() => dispatch({ type: "goto", step: s.id })}
                  UNSAFE_style={{ justifyContent: "flex-start" }}
                >
                  {s.label}
                </ToggleButton>
              ))}
            </Flex>
          </nav>
          {state.source && (
            <View marginTop="size-200">
              <Text UNSAFE_style={{ opacity: 0.7, fontSize: 12, wordBreak: "break-all" }}>{state.source.description}</Text>
            </View>
          )}
        </Flex>
      </View>
      <View flex padding="size-300" overflow="auto">
        {state.step === "connect" && <Connect state={state} dispatch={dispatch} />}
        {state.step === "plan" && state.source && <PlanStep state={state} dispatch={dispatch} />}
        {state.step === "review" && state.plan && <Review state={state} dispatch={dispatch} />}
        {state.step === "results" && state.runId && <Results key={state.runId} state={state} dispatch={dispatch} />}
      </View>
    </Flex>
  );
}
