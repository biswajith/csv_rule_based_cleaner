import { Badge, Cell, Column, Row, TableBody, TableHeader, TableView, Text } from "@adobe/react-spectrum";
import type { Profile } from "../types";

export function ProfileTable({ profile }: { profile: Profile }) {
  const n = profile.sampled_rows || 1;
  return (
    <TableView aria-label="Column profile" height="size-4600" overflowMode="wrap">
      <TableHeader>
        <Column key="name" isRowHeader width="22%">Column</Column>
        <Column key="type" width="12%">Type</Column>
        <Column key="empty" width="10%">Empty</Column>
        <Column key="distinct" width="12%">Distinct</Column>
        <Column key="shapes">Top shapes</Column>
      </TableHeader>
      <TableBody items={profile.columns.map((c) => ({ ...c, id: `col:${c.name}` }))}>
        {(c) => (
          <Row key={c.id}>
            <Cell>
              <Text UNSAFE_style={{ opacity: c.cleanable ? 1 : 0.6 }}>
                {c.name}
                {!c.cleanable && " "}
              </Text>
              {!c.cleanable && <Badge variant="neutral">read-only</Badge>}
            </Cell>
            <Cell>{c.inferred_type}</Cell>
            <Cell>{`${Math.round((c.empty / n) * 100)}%`}</Cell>
            <Cell>{String(c.distinct_count)}</Cell>
            <Cell>
              <code>
                {c.shapes
                  .slice(0, 3)
                  .map((s) => `${s.shape} (${s.count})`)
                  .join("   ")}
              </code>
            </Cell>
          </Row>
        )}
      </TableBody>
    </TableView>
  );
}
