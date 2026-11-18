import { useCallback, useEffect, useRef, useState } from "react";
import {
  Button, Cell, Column, Flex, Heading, InlineAlert, Content, Item, Picker, ProgressBar, Row, TableBody,
  TableHeader, TableView, Text, View,
} from "@adobe/react-spectrum";
import { api, fileUrl } from "../api";
import { ApiError } from "../apiError";
import { ErrorBanner, RESTART_NOTICE, toApiError } from "../components/ErrorBanner";
import type { Action, AppState } from "../state";
import type { Change, Flag, Page, RunReport, RunStatus } from "../types";

const PAGE = 100;
const ALL = "__all__";

const PHASE_LABEL: Record<string, string> = {
  scanning: "Scanning for cells that need the LLM",
  resolving: "Asking the LLM to fix cells",
  writing: "Writing cleaned data",
};

export function Results({ state, dispatch }: { state: AppState; dispatch: (a: Action) => void }) {
  const runId = state.runId!;
  const [status, setStatus] = useState<RunStatus | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [cancelling, setCancelling] = useState(false);
  const stopped = useRef(false);

  // Only a 404 on the run status itself means the server forgot the run (restart).
  const failStatus = useCallback(
    (e: unknown) => {
      const err = toApiError(e);
      if (err.status === 404) dispatch({ type: "reset", notice: RESTART_NOTICE });
      else setError(err);
    },
    [dispatch],
  );
  // Elsewhere (changes/flags/cancel) a 404 is an ordinary error.
  const fail = useCallback((e: unknown) => setError(toApiError(e)), []);

  // Poll once a second while queued/running.
  useEffect(() => {
    stopped.current = false;
    let timer: number | undefined;
    const tick = async () => {
      try {
        const s = await api.run(runId);
        if (stopped.current) return;
        setStatus(s);
        setError(null);
        if (s.status === "queued" || s.status === "running") timer = window.setTimeout(tick, 1000);
      } catch (e) {
        if (stopped.current) return;
        const err = toApiError(e);
        if (err.status === 0 || err.status >= 500) {
          setError(err); // transient: keep polling
          timer = window.setTimeout(tick, 2000);
        } else failStatus(err);
      }
    };
    void tick();
    return () => {
      stopped.current = true;
      if (timer) window.clearTimeout(timer);
    };
  }, [runId, failStatus]);

  async function cancel() {
    setCancelling(true);
    try {
      setStatus(await api.cancel(runId));
    } catch (e) {
      failStatus(e);
    } finally {
      setCancelling(false);
    }
  }

  const active = !status || status.status === "queued" || status.status === "running";
  const report = status?.report ?? null;

  return (
    <Flex direction="column" gap="size-200">
      <Heading level={2}>Results</Heading>
      <Text UNSAFE_style={{ opacity: 0.7 }}>Run {runId}</Text>
      <ErrorBanner error={error} title="Could not read run status" />

      {active && (
        <Flex direction="column" gap="size-100" maxWidth="size-6000">
          <ProgressBar
            label={
              !status || status.status === "queued"
                ? "Queued"
                : (status.phase && PHASE_LABEL[status.phase]) || "Running"
            }
            isIndeterminate={!status || status.status === "queued" || !status.total}
            value={status?.total ? Math.min(100, Math.round((status.done / status.total) * 100)) : 0}
            showValueLabel={!!status?.total}
            width="100%"
          />
          <Flex>
            <Button variant="secondary" isPending={cancelling} onPress={cancel}>
              Cancel run
            </Button>
          </Flex>
        </Flex>
      )}

      {status && (status.status === "failed" || status.status === "cancelled") && (
        <InlineAlert variant={status.status === "failed" ? "negative" : "notice"}>
          <Heading>{status.status === "failed" ? "Run failed" : "Run cancelled"}</Heading>
          <Content>
            {status.error || report?.error || (status.status === "cancelled" ? "Run was cancelled." : "Unknown error.")}
            {report && report.in_place && (
              <View marginTop="size-100">
                {report.rows_written} row(s) had already been written to the source before the run stopped. The
                changes file lists the original values.
              </View>
            )}
            {report && !report.in_place && report.rows_written > 0 && (
              <View marginTop="size-100">{report.rows_written} row(s) were written to the output before the run stopped.</View>
            )}
          </Content>
        </InlineAlert>
      )}

      {report && <Summary report={report} />}

      {status && !active && status.files.length > 0 && (
        <Flex direction="column" gap="size-100">
          <Heading level={3}>Files</Heading>
          <Flex gap="size-150" wrap>
            {status.files.map((f) => (
              <a key={f} href={fileUrl(runId, f)} download={f} style={{ textDecoration: "underline", color: "var(--spectrum-global-color-blue-600)" }}>
                {f}
              </a>
            ))}
          </Flex>
        </Flex>
      )}

      {report && (
        <>
          <PagedTable<Change>
            title="Changes"
            emptyText="No changes."
            columnNames={report.columns.map((c) => c.column)}
            fetchPage={(c, o) => api.changes(runId, c, o, PAGE)}
            onError={fail}
            height="size-4600"
            columns={[
              { key: "row", label: "Row", width: 90 },
              { key: "column", label: "Column", width: "18%" },
              { key: "before", label: "Before", isRowHeader: true },
              { key: "after", label: "After" },
              { key: "sources", label: "Sources", width: "20%" },
            ]}
            cells={(c) => [String(c.row_key), c.column, cellText(c.before), cellText(c.after), c.sources.join(", ")]}
          />
          <PagedTable<Flag>
            title="Flags"
            emptyText="No flags."
            columnNames={report.columns.map((c) => c.column)}
            fetchPage={(c, o) => api.flags(runId, c, o, PAGE)}
            onError={fail}
            height="size-3600"
            columns={[
              { key: "row", label: "Row", width: 90 },
              { key: "column", label: "Column", width: "18%" },
              { key: "value", label: "Value", isRowHeader: true },
              { key: "message", label: "Message" },
            ]}
            cells={(f) => [String(f.row_key), f.column, cellText(f.value), f.message]}
          />
        </>
      )}
    </Flex>
  );
}

function Tile({ label, value }: { label: string; value: string }) {
  return (
    <View borderWidth="thin" borderColor="dark" borderRadius="medium" padding="size-150" minWidth="size-1600">
      <Flex direction="column">
        <Text UNSAFE_style={{ fontSize: 22, fontWeight: 700 }}>{value}</Text>
        <Text UNSAFE_style={{ opacity: 0.75 }}>{label}</Text>
      </Flex>
    </View>
  );
}

function Summary({ report }: { report: RunReport }) {
  const n = (x: number) => x.toLocaleString();
  const l = report.llm;
  return (
    <Flex direction="column" gap="size-100">
      {report.approval && (
        <Text>
          Plan approved by {report.approval.by} on {new Date(report.approval.at).toLocaleString()}
        </Text>
      )}
      <Text>
        {report.source} → {report.target}
        {report.in_place ? " (in place)" : ""}
      </Text>
      <Flex gap="size-150" wrap>
        <Tile label="Rows" value={n(report.rows)} />
        <Tile label="Cells changed" value={n(report.cells_changed)} />
        <Tile label="Flags" value={n(report.flags_written)} />
        <Tile label="LLM cells sent / fixed" value={`${n(l.cells_sent)} / ${n(l.cells_fixed)}`} />
        <Tile label="LLM calls" value={`${n(l.calls)}${l.failed_calls ? ` (${l.failed_calls} failed)` : ""}`} />
        <Tile label="Tokens in / out" value={`${n(l.input_tokens)} / ${n(l.output_tokens)}`} />
        <Tile label="Duration" value={`${report.duration_s.toFixed(1)} s`} />
      </Flex>
      {l.cells_over_budget > 0 && (
        <Text>{n(l.cells_over_budget)} cell(s) exceeded the LLM budget and were flagged instead.</Text>
      )}
    </Flex>
  );
}

/** Shared paging + column-filter logic for the changes and flags tables. */
function usePaged<T>(
  fetchPage: (column: string, offset: number) => Promise<Page<T>>,
  column: string,
  onError: (e: unknown) => void,
) {
  const [items, setItems] = useState<T[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const gen = useRef(0);
  const fetchRef = useRef(fetchPage);
  fetchRef.current = fetchPage;

  const load = useCallback(
    async (offset: number) => {
      const g = gen.current;
      setLoading(true);
      try {
        const p = await fetchRef.current(column, offset);
        if (g !== gen.current) return;
        setTotal(p.total);
        setItems((prev) => (offset === 0 ? p.items : [...prev, ...p.items]));
      } catch (e) {
        if (g === gen.current) onError(e);
      } finally {
        if (g === gen.current) setLoading(false);
      }
    },
    [column, onError],
  );

  useEffect(() => {
    gen.current++;
    setItems([]);
    setTotal(0);
    void load(0);
  }, [load]);

  return { items, total, loading, more: () => void load(items.length) };
}

function ColumnFilter({
  columnNames, value, onChange, label,
}: {
  columnNames: string[];
  value: string;
  onChange: (c: string) => void;
  label: string;
}) {
  return (
    <Picker label={label} selectedKey={value || ALL} onSelectionChange={(k) => onChange(k === ALL ? "" : String(k))}>
      {[<Item key={ALL}>All columns</Item>, ...columnNames.map((c) => <Item key={c}>{c}</Item>)]}
    </Picker>
  );
}

const cellText = (s: string) => (s === "" ? "∅" : s);

interface ColumnDef {
  key: string;
  label: string;
  width?: number | `${number}%`;
  isRowHeader?: boolean;
}

/** Generic paged, column-filterable table used for both changes and flags. */
function PagedTable<T>({
  title, emptyText, columnNames, fetchPage, onError, height, columns, cells,
}: {
  title: string;
  emptyText: string;
  columnNames: string[];
  fetchPage: (column: string, offset: number) => Promise<Page<T>>;
  onError: (e: unknown) => void;
  height: string;
  columns: ColumnDef[];
  cells: (item: T) => string[];
}) {
  const [column, setColumn] = useState("");
  const { items, total, loading, more } = usePaged<T>(fetchPage, column, onError);
  return (
    <Flex direction="column" gap="size-100">
      <Flex alignItems="end" gap="size-200" wrap>
        <Heading level={3} margin={0}>
          {title}
        </Heading>
        <ColumnFilter
          columnNames={columnNames}
          value={column}
          onChange={setColumn}
          label={`Filter ${title.toLowerCase()} by column`}
        />
        <Text>
          Showing {items.length} of {total}
        </Text>
      </Flex>
      <TableView
        aria-label={title}
        height={height as "size-4600"}
        overflowMode="wrap"
        renderEmptyState={() => <Text>{loading ? "Loading…" : emptyText}</Text>}
      >
        <TableHeader>
          {columns.map((c) => (
            <Column key={c.key} width={c.width} isRowHeader={c.isRowHeader}>
              {c.label}
            </Column>
          ))}
        </TableHeader>
        {/* String ids: React Spectrum treats a falsy key (row 0) as missing and crashes. */}
        <TableBody items={items.map((it, i) => ({ id: `r${i}`, cells: cells(it) }))}>
          {(row) => (
            <Row key={row.id}>
              {row.cells.map((t, i) => (
                // Cell keys are collection-wide in React Spectrum: must be unique across rows.
                <Cell key={`${row.id}-${i}`}>{t}</Cell>
              ))}
            </Row>
          )}
        </TableBody>
      </TableView>
      {items.length < total && (
        <Flex>
          <Button variant="secondary" isPending={loading} onPress={more}>
            Load more
          </Button>
        </Flex>
      )}
    </Flex>
  );
}
