import { useState } from "react";
import {
  ActionButton, Badge, Button, ButtonGroup, Content, Dialog, DialogTrigger, Divider, Flex, Heading,
  Switch, Text, TextArea, View,
} from "@adobe/react-spectrum";
import Delete from "@spectrum-icons/workflow/Delete";
import Edit from "@spectrum-icons/workflow/Edit";
import { ApiError } from "../apiError";
import { ErrorBanner, toApiError } from "../components/ErrorBanner";
import { hasNoEffect } from "../noEffect";
import { opSummary } from "../opSummary";
import type { Action } from "../state";
import type { ColumnPlan, ColumnPreview } from "../types";

interface Props {
  index: number;
  column: ColumnPlan;
  preview: ColumnPreview | undefined;
  stale: boolean;
  dispatch: (a: Action) => void;
  /** Validate the whole plan with this column replaced and adopt the server's normalised plan; throws ApiError. */
  onEditJson: (index: number, column: ColumnPlan) => Promise<void>;
}

const mono = { fontFamily: "monospace", whiteSpace: "pre-wrap", wordBreak: "break-all" } as const;

function show(s: string): string {
  return s === "" ? "∅ (empty)" : s;
}

export function ColumnCard({ index, column, preview, stale, dispatch, onEditJson }: Props) {
  const stats = stale ? undefined : preview;
  return (
    <View borderWidth="thin" borderColor="dark" borderRadius="medium" padding="size-200">
      <Flex direction="column" gap="size-150">
        <Flex justifyContent="space-between" alignItems="center" wrap gap="size-100">
          <Heading level={3} margin={0}>
            {column.column}
          </Heading>
          <Flex gap="size-100" alignItems="center">
            {stats && (
              <>
                <Badge variant="info">{stats.cells_changed} changed</Badge>
                <Badge variant={stats.cells_flagged ? "yellow" : "neutral"}>{stats.cells_flagged} flagged</Badge>
              </>
            )}
            <EditJsonDialog column={column} index={index} onSave={onEditJson} />
          </Flex>
        </Flex>
        {column.rationale && <Text UNSAFE_style={{ opacity: 0.8 }}>{column.rationale}</Text>}

        <Flex direction="column" gap="size-75" aria-label={`Operations for ${column.column}`}>
          {column.ops.length === 0 && <Text>No operations.</Text>}
          {column.ops.map((op, i) => {
            const enabled = op.enabled !== false;
            const st = stats?.ops.find((s) => s.op_index === i);
            const noEffect = hasNoEffect(op, st, stats);
            return (
              <Flex key={i} alignItems="center" gap="size-150" wrap UNSAFE_style={{ opacity: enabled ? 1 : 0.55 }}>
                <Switch
                  aria-label={`Enable ${op.op} on ${column.column}`}
                  isSelected={enabled}
                  onChange={(v) => dispatch({ type: "toggle_op", col: index, op: i, enabled: v })}
                />
                <Badge variant="neutral">{op.op}</Badge>
                <Text UNSAFE_style={{ ...mono, flex: "1 1 240px" }}>{opSummary(op)}</Text>
                {op.note && <Text UNSAFE_style={{ opacity: 0.7 }}>{op.note}</Text>}
                {st && enabled && (st.changed > 0 || st.flagged > 0) && (
                  <Text UNSAFE_style={{ whiteSpace: "nowrap" }}>
                    {st.changed} changed · {st.flagged} flagged
                  </Text>
                )}
                {noEffect && <Badge variant="yellow">no effect on sample</Badge>}
                <ActionButton
                  isQuiet
                  aria-label={`Delete ${op.op} from ${column.column}`}
                  onPress={() => dispatch({ type: "delete_op", col: index, op: i })}
                >
                  <Delete />
                </ActionButton>
              </Flex>
            );
          })}
        </Flex>

        {stats && (stats.examples.length > 0 || stats.flag_examples.length > 0) && (
          <>
            <Divider size="S" />
            <Flex gap="size-300" wrap>
              {stats.examples.length > 0 && (
                <Flex direction="column" gap="size-50" flex="1 1 300px">
                  <Text UNSAFE_style={{ fontWeight: 600 }}>Before → after</Text>
                  {stats.examples.map(([b, a], i) => (
                    <Text key={i} UNSAFE_style={mono}>
                      {show(b)} → {show(a)}
                    </Text>
                  ))}
                </Flex>
              )}
              {stats.flag_examples.length > 0 && (
                <Flex direction="column" gap="size-50" flex="1 1 300px">
                  <Text UNSAFE_style={{ fontWeight: 600 }}>Flagged</Text>
                  {stats.flag_examples.map(([v, m], i) => (
                    <Text key={i} UNSAFE_style={mono}>
                      {show(v)} — {m}
                    </Text>
                  ))}
                </Flex>
              )}
            </Flex>
          </>
        )}
      </Flex>
    </View>
  );
}

function EditJsonDialog({
  column, index, onSave,
}: {
  column: ColumnPlan;
  index: number;
  onSave: (index: number, column: ColumnPlan) => Promise<void>;
}) {
  const [text, setText] = useState("");
  const [error, setError] = useState<ApiError | null>(null);
  const [busy, setBusy] = useState(false);

  async function save(close: () => void) {
    setError(null);
    let parsed: ColumnPlan;
    try {
      parsed = JSON.parse(text) as ColumnPlan;
    } catch (e) {
      setError(new ApiError(0, `Invalid JSON: ${e instanceof Error ? e.message : String(e)}`));
      return;
    }
    setBusy(true);
    try {
      await onSave(index, parsed);
      close();
    } catch (e) {
      setError(toApiError(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <DialogTrigger
      type="modal"
      onOpenChange={(open) => {
        if (open) {
          setText(JSON.stringify(column, null, 2));
          setError(null);
        }
      }}
    >
      <ActionButton aria-label={`Edit JSON for ${column.column}`}>
        <Edit />
        <Text>Edit JSON</Text>
      </ActionButton>
      {(close) => (
        <Dialog size="L">
          <Heading>Edit column plan: {column.column}</Heading>
          <Divider />
          <Content>
            <Flex direction="column" gap="size-150">
              <TextArea
                label="Column plan JSON"
                value={text}
                onChange={setText}
                width="100%"
                height="size-4600"
                UNSAFE_style={{ fontFamily: "monospace" }}
              />
              <ErrorBanner error={error} title="Plan not accepted" />
            </Flex>
          </Content>
          <ButtonGroup>
            <Button variant="secondary" onPress={close}>
              Cancel
            </Button>
            <Button variant="accent" isPending={busy} onPress={() => save(close)}>
              Validate and apply
            </Button>
          </ButtonGroup>
        </Dialog>
      )}
    </DialogTrigger>
  );
}
