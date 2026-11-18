import type { Op } from "./types";

function q(s: string, max = 40): string {
  const t = s.length > max ? s.slice(0, max - 1) + "…" : s;
  return `"${t}"`;
}

function mapSize(m: Record<string, string>): string {
  const n = Object.keys(m).length;
  return `${n} ${n === 1 ? "entry" : "entries"}`;
}

export function opSummary(op: Op): string {
  switch (op.op) {
    case "trim":
      return "strip leading/trailing whitespace";
    case "collapse_whitespace":
      return "collapse runs of whitespace to one space";
    case "case":
      return `${op.mode}case`.replace("titlecase", "Title Case");
    case "regex_replace":
      return `/${op.pattern}/${op.ignore_case ? "i" : ""} → ${q(op.replacement ?? "")}`;
    case "reformat":
      return `/${op.pattern}/${op.ignore_case ? "i" : ""} (full match) → ${q(op.template)}`;
    case "translate":
      return `translate characters (${mapSize(op.mapping)})`;
    case "map_values":
      return `map values (${mapSize(op.mapping)})`;
    case "map_from_column":
      return `from column ${q(op.source_column)} via lookup (${mapSize(op.mapping)}), ${op.mode ?? "overwrite"}`;
    case "null_if": {
      const parts: string[] = [];
      if (op.values && op.values.length) parts.push(`one of ${op.values.map((v) => q(v, 20)).join(", ")}`);
      if (op.pattern) parts.push(`/${op.pattern}/`);
      return `set to null if ${parts.join(" or ") || "(nothing)"}`;
    }
    case "parse_date":
      return `→ ${op.output_format ?? "%Y-%m-%d"}${op.day_first ? " (day first)" : ""}`;
    case "validate":
      return `flag unless /${op.pattern}/ — ${q(op.message, 50)}`;
    case "llm_fix":
      return `${op.when ?? "if_flagged"}: ${q(op.instruction, 60)}`;
    default:
      return JSON.stringify(op);
  }
}
