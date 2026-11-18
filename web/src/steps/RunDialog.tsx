import { useState } from "react";
import {
  AlertDialog, Button, ButtonGroup, Content, Dialog, DialogContainer, Divider, Flex, Heading, Switch, Text,
} from "@adobe/react-spectrum";
import { api } from "../api";
import { ApiError } from "../apiError";
import { ErrorBanner, RESTART_NOTICE, toApiError } from "../components/ErrorBanner";
import type { Action } from "../state";
import type { Plan, RunOptions, SourceInfo } from "../types";

interface Props {
  source: SourceInfo;
  plan: Plan;
  hasLlmFix: boolean;
  isDisabled: boolean;
  dispatch: (a: Action) => void;
}

export function RunButton({ source, plan, hasLlmFix, isDisabled, dispatch }: Props) {
  const [stage, setStage] = useState<"closed" | "options" | "confirm">("closed");
  const [inPlace, setInPlace] = useState(false);
  const [noLlm, setNoLlm] = useState(false);
  const [overwrite, setOverwrite] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);

  const canInPlace = source.kind !== "csv" && source.can_in_place;
  const targetName =
    source.kind === "sql" ? "table" : source.kind === "mongo" ? "collection" : "file";

  async function start(opts: RunOptions) {
    setBusy(true);
    setError(null);
    try {
      const { run_id } = await api.startRun(source.source_id, plan, opts);
      setStage("closed");
      dispatch({ type: "run_started", runId: run_id });
    } catch (e) {
      const err = toApiError(e);
      if (err.status === 404) {
        setStage("closed");
        dispatch({ type: "reset", notice: RESTART_NOTICE });
      } else {
        setError(err);
        setStage("options");
      }
    } finally {
      setBusy(false);
    }
  }

  const options = (over: boolean): RunOptions => ({
    in_place: canInPlace && inPlace,
    overwrite: over,
    no_llm: noLlm,
  });

  function submit() {
    if (canInPlace && inPlace) setStage("confirm");
    else void start(options(overwrite));
  }

  return (
    <>
      <Button
        variant="accent"
        isDisabled={isDisabled}
        onPress={() => {
          setError(null);
          setStage("options");
        }}
      >
        Run cleaning
      </Button>
      <DialogContainer onDismiss={() => setStage("closed")}>
        {stage === "options" && (
          <Dialog>
            <Heading>Run cleaning</Heading>
            <Divider />
            <Content>
              <Flex direction="column" gap="size-100">
                <Text>
                  Source: {source.description}. Records are never deleted; the cleaned data is written to a new{" "}
                  {targetName} unless you choose in-place.
                </Text>
                {canInPlace && (
                  <Switch isSelected={inPlace} onChange={setInPlace}>
                    Modify the source {targetName} in place
                  </Switch>
                )}
                <Switch isSelected={noLlm} onChange={setNoLlm} isDisabled={!hasLlmFix}>
                  Skip LLM steps (flag those cells instead)
                </Switch>
                {source.kind !== "csv" && !inPlace && (
                  <Switch isSelected={overwrite} onChange={setOverwrite}>
                    Overwrite the existing output {targetName} if it exists
                  </Switch>
                )}
                <ErrorBanner error={error} title="Could not start the run" />
                {error?.status === 409 && (
                  <Flex>
                    <Button variant="negative" isPending={busy} onPress={() => void start(options(true))}>
                      Overwrite and retry
                    </Button>
                  </Flex>
                )}
              </Flex>
            </Content>
            <ButtonGroup>
              <Button variant="secondary" onPress={() => setStage("closed")}>
                Cancel
              </Button>
              <Button variant="accent" isPending={busy} onPress={submit}>
                Start
              </Button>
            </ButtonGroup>
          </Dialog>
        )}
        {stage === "confirm" && (
          <AlertDialog
            variant="destructive"
            title="Modify the source in place?"
            primaryActionLabel="Modify in place"
            cancelLabel="Back"
            onPrimaryAction={() => void start(options(false))}
            onCancel={() => setStage("options")}
          >
            This will update rows directly in {source.description}. Batches already written stay written if the
            run fails or is cancelled; the changes file lists the original values so you can undo them.
          </AlertDialog>
        )}
      </DialogContainer>
    </>
  );
}
