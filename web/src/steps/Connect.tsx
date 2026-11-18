import { useState } from "react";
import {
  Button, Content, DropZone, FileTrigger, Flex, Heading, IllustratedMessage, Item, Picker,
  ProgressCircle, TabList, TabPanels, Tabs, Text, TextField, View, Well,
} from "@adobe/react-spectrum";
import Upload from "@spectrum-icons/workflow/UploadToCloud";
import { api } from "../api";
import { ApiError } from "../apiError";
import { ErrorBanner, toApiError } from "../components/ErrorBanner";
import { ProfileTable } from "./ProfileTable";
import type { Action, AppState } from "../state";
import type { SourceInfo } from "../types";

const DELIMITERS: { key: string; label: string; value: string }[] = [
  { key: "comma", label: "Comma ,", value: "," },
  { key: "semicolon", label: "Semicolon ;", value: ";" },
  { key: "tab", label: "Tab", value: "\t" },
  { key: "pipe", label: "Pipe |", value: "|" },
];

interface Props {
  state: AppState;
  dispatch: (a: Action) => void;
}

export function Connect({ state, dispatch }: Props) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);

  // CSV
  const [file, setFile] = useState<File | null>(null);
  const [delimiter, setDelimiter] = useState("comma");
  const [encoding, setEncoding] = useState("utf-8-sig");
  // SQL
  const [sqlUrl, setSqlUrl] = useState("");
  const [table, setTable] = useState("");
  const [schema, setSchema] = useState("");
  const [keyColumn, setKeyColumn] = useState("");
  // Mongo
  const [mongoUri, setMongoUri] = useState("");
  const [database, setDatabase] = useState("");
  const [collection, setCollection] = useState("");
  const [tab, setTab] = useState<string>("csv");

  async function connect(make: () => Promise<SourceInfo>) {
    setBusy(true);
    setError(null);
    try {
      const source = await make();
      dispatch({ type: "source", source });
      try {
        const profile = await api.profile(source.source_id);
        dispatch({ type: "profile", sourceId: source.source_id, profile });
      } catch (e) {
        dispatch({ type: "profile_failed", sourceId: source.source_id, error: toApiError(e).detail });
      }
    } catch (e) {
      setError(toApiError(e));
    } finally {
      setBusy(false);
    }
  }

  const connectCsv = () => file && connect(() => api.upload(file, DELIMITERS.find((d) => d.key === delimiter)?.value ?? ",", encoding));
  const connectSql = () =>
    connect(() =>
      api.addSource({
        kind: "sql",
        url: sqlUrl,
        table,
        ...(schema.trim() ? { schema: schema.trim() } : {}),
        ...(keyColumn.trim() ? { key_column: keyColumn.trim() } : {}),
      }),
    );
  const connectMongo = () =>
    connect(() => api.addSource({ kind: "mongo", uri: mongoUri, database, collection }));

  const canConnect =
    !busy &&
    (tab === "csv" ? !!file : tab === "sql" ? !!sqlUrl && !!table.trim() : !!mongoUri && !!database.trim() && !!collection.trim());
  const doConnect = tab === "csv" ? connectCsv : tab === "sql" ? connectSql : connectMongo;

  return (
    <Flex direction="column" gap="size-200">
      <Heading level={2}>Connect a data source</Heading>
      {state.notice && (
        <Well role="region" aria-label="Notice">{state.notice}</Well>
      )}
      <Tabs aria-label="Source type" selectedKey={tab} onSelectionChange={(k) => setTab(String(k))}>
        <TabList>
          <Item key="csv">CSV</Item>
          <Item key="sql">SQL</Item>
          <Item key="mongo">MongoDB</Item>
        </TabList>
        <TabPanels>
          <Item key="csv">
            <Flex direction="column" gap="size-150" marginTop="size-150">
              <DropZone
                maxWidth="size-6000"
                onDrop={async (e) => {
                  const it = e.items.find((i) => i.kind === "file");
                  if (it && it.kind === "file") setFile(await it.getFile());
                }}
              >
                <IllustratedMessage>
                  <Upload size="L" />
                  <Heading>{file ? file.name : "Drop a CSV file here"}</Heading>
                  <Content>
                    <FileTrigger
                      acceptedFileTypes={[".csv", "text/csv", ".tsv", ".txt"]}
                      onSelect={(files) => {
                        const f = files?.[0];
                        if (f) setFile(f);
                      }}
                    >
                      <Button variant="secondary">Choose file</Button>
                    </FileTrigger>
                    {state.config && (
                      <View marginTop="size-100">
                        <Text>Up to {state.config.max_upload_mb} MB</Text>
                      </View>
                    )}
                  </Content>
                </IllustratedMessage>
              </DropZone>
              <Flex gap="size-150" wrap>
                <Picker label="Delimiter" selectedKey={delimiter} onSelectionChange={(k) => setDelimiter(String(k))}>
                  {DELIMITERS.map((d) => (
                    <Item key={d.key}>{d.label}</Item>
                  ))}
                </Picker>
                <TextField label="Encoding" value={encoding} onChange={setEncoding} width="size-2000" />
              </Flex>
            </Flex>
          </Item>
          <Item key="sql">
            <Flex direction="column" gap="size-150" marginTop="size-150" maxWidth="size-6000">
              <TextField
                label="SQLAlchemy URL"
                type="password"
                autoComplete="off"
                value={sqlUrl}
                onChange={setSqlUrl}
                description="Kept in server memory only; never displayed."
                width="100%"
              />
              <Flex gap="size-150" wrap>
                <TextField label="Table" value={table} onChange={setTable} isRequired />
                <TextField label="Schema (optional)" value={schema} onChange={setSchema} />
                <TextField label="Key column (optional)" value={keyColumn} onChange={setKeyColumn} />
              </Flex>
              <Text UNSAFE_style={{ opacity: 0.7 }}>
                The table needs a single-column unique key (its primary key, or the key column named here).
                The profile samples the first {state.config?.sample_size ?? "N"} rows by key.
              </Text>
            </Flex>
          </Item>
          <Item key="mongo">
            <Flex direction="column" gap="size-150" marginTop="size-150" maxWidth="size-6000">
              <TextField
                label="MongoDB URI"
                type="password"
                autoComplete="off"
                value={mongoUri}
                onChange={setMongoUri}
                description="Kept in server memory only; never displayed."
                width="100%"
              />
              <Flex gap="size-150" wrap>
                <TextField label="Database" value={database} onChange={setDatabase} isRequired />
                <TextField label="Collection" value={collection} onChange={setCollection} isRequired />
              </Flex>
            </Flex>
          </Item>
        </TabPanels>
      </Tabs>
      <Flex gap="size-150" alignItems="center">
        <Button variant="accent" isDisabled={!canConnect} onPress={doConnect}>
          Connect
        </Button>
        {busy && <ProgressCircle aria-label="Connecting" isIndeterminate size="S" />}
      </Flex>
      <ErrorBanner error={error} title="Could not connect" />

      {state.source && (
        <Flex direction="column" gap="size-150">
          <Heading level={3}>Profile</Heading>
          <Text>
            {state.source.description} · {state.source.count ?? "unknown"} rows
            {state.profile && ` · profiled ${state.profile.sampled_rows} sampled rows`}
          </Text>
          {state.profile ? (
            <ProfileTable profile={state.profile} />
          ) : state.profileError ? (
            <ErrorBanner error={new ApiError(0, state.profileError)} title="Could not profile the source" />
          ) : (
            <ProgressCircle aria-label="Profiling" isIndeterminate />
          )}
          <Flex>
            <Button variant="accent" onPress={() => dispatch({ type: "goto", step: "plan" })}>
              Continue to Plan
            </Button>
          </Flex>
        </Flex>
      )}
    </Flex>
  );
}

