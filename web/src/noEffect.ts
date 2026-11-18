import type { ColumnPreview, Op, OpStat } from "./types";

/** Ops that are expected to change or flag something when they do anything useful. */
const WARN_OPS = new Set([
  "regex_replace", "reformat", "translate", "map_values", "map_from_column", "null_if", "parse_date",
]);

/**
 * "No effect on sample" rule (SPEC C4). Warn for an enabled op only when it changed and flagged
 * nothing and is one of WARN_OPS; llm_fix warns only when the column sent no cells to the LLM.
 */
export function hasNoEffect(op: Op, stat: OpStat | undefined, col: ColumnPreview | undefined): boolean {
  if (op.enabled === false || !stat || !col) return false;
  if (stat.changed !== 0 || stat.flagged !== 0) return false;
  if (op.op === "llm_fix") return col.llm_cells === 0;
  return WARN_OPS.has(op.op);
}
