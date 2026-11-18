"""The Plan: the reviewable contract between planner and cleaner (spec §4)."""

from __future__ import annotations

from functools import cached_property
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _Op(_Model):
    enabled: bool = True
    note: str = ""


class Trim(_Op):
    op: Literal["trim"] = "trim"


class CollapseWhitespace(_Op):
    op: Literal["collapse_whitespace"] = "collapse_whitespace"


class Case(_Op):
    op: Literal["case"] = "case"
    mode: Literal["lower", "upper", "title"]


class RegexReplace(_Op):
    op: Literal["regex_replace"] = "regex_replace"
    pattern: str
    replacement: str = ""
    ignore_case: bool = False


class Reformat(_Op):
    op: Literal["reformat"] = "reformat"
    pattern: str
    template: str
    ignore_case: bool = False


class Translate(_Op):
    op: Literal["translate"] = "translate"
    mapping: dict[str, str]
    ignore_case: bool = False


class MapValues(_Op):
    op: Literal["map_values"] = "map_values"
    mapping: dict[str, str]
    ignore_case: bool = True

    @cached_property
    def folded(self) -> dict[str, str]:
        """casefolded key -> mapped value; first key in mapping order wins (computed lazily)."""
        out: dict[str, str] = {}
        for k, val in self.mapping.items():
            out.setdefault(k.casefold(), val)
        return out


class MapFromColumn(_Op):
    op: Literal["map_from_column"] = "map_from_column"
    source_column: str
    mapping: dict[str, str]
    ignore_case: bool = True
    mode: Literal["overwrite", "fill_empty"] = "overwrite"

    @cached_property
    def folded(self) -> dict[str, str]:
        """casefolded key -> mapped value; first key in mapping order wins (computed lazily)."""
        out: dict[str, str] = {}
        for k, val in self.mapping.items():
            out.setdefault(k.casefold(), val)
        return out


class NullIf(_Op):
    op: Literal["null_if"] = "null_if"
    values: list[str] = Field(default_factory=list)
    pattern: str | None = None


class ParseDate(_Op):
    op: Literal["parse_date"] = "parse_date"
    output_format: str = "%Y-%m-%d"
    day_first: bool = False


class Validate(_Op):
    op: Literal["validate"] = "validate"
    pattern: str
    message: str
    ignore_case: bool = False
    allow_empty: bool = True


class LLMFix(_Op):
    op: Literal["llm_fix"] = "llm_fix"
    instruction: str
    when: Literal["if_flagged", "if_not_match", "always"] = "if_flagged"
    pattern: str | None = None
    context_columns: list[str] = Field(default_factory=list)


Op = Annotated[
    Union[
        Trim, CollapseWhitespace, Case, RegexReplace, Reformat, Translate, MapValues,
        MapFromColumn, NullIf, ParseDate, Validate, LLMFix,
    ],
    Field(discriminator="op"),
]

OP_NAMES: tuple[str, ...] = (
    "trim", "collapse_whitespace", "case", "regex_replace", "reformat", "translate",
    "map_values", "map_from_column", "null_if", "parse_date", "validate", "llm_fix",
)


class ColumnPlan(_Model):
    column: str
    rationale: str = ""
    ops: list[Op] = Field(default_factory=list)


class Plan(_Model):
    version: Literal[1] = 1
    rules: list[str] = Field(default_factory=list)
    columns: list[ColumnPlan] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)

    def has_llm_fix(self) -> bool:
        return any(o.enabled and isinstance(o, LLMFix) for cp in self.columns for o in cp.ops)


class PlannerOutput(_Model):
    """What the planner LLM returns; code adds `version` and `rules`."""

    columns: list[ColumnPlan] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class RawJSON(BaseModel):
    """Any JSON object. The planner validates the shape itself (so schema errors feed the same
    repair loop as check_plan errors)."""

    model_config = ConfigDict(extra="allow")
