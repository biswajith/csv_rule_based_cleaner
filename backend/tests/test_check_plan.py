from __future__ import annotations

import re

import pytest
from pydantic import ValidationError
from util import infos, mkplan

from scrubber.connectors.base import ColumnInfo
from scrubber.ops import check_in_place, check_plan
from scrubber.plan import Plan

COLS = infos("a", "b", "c")


def errs(plan, cols=COLS):
    return check_plan(plan, cols)[0]


def one(plan, needle, cols=COLS):
    e = errs(plan, cols)
    assert any(needle in x for x in e), e


def test_valid_plan_has_no_errors():
    plan = mkplan(("a", [{"op": "trim"}, {"op": "validate", "pattern": "x", "message": "m"}]))
    assert check_plan(plan, COLS) == ([], [])


@pytest.mark.parametrize("bad", [
    {"op": "nope"}, {"op": "trim", "extra": 1}, {"op": "case"}, {"op": "case", "mode": "weird"},
    {"op": "regex_replace", "pattern": "a", "ignorecase": True},
    {"op": "map_from_column", "source_column": "b", "mapping": {}, "mode": "only_if_mismatch"},
])
def test_r1_schema_errors(bad):
    with pytest.raises(ValidationError):
        mkplan(("a", [bad]))


def test_r1_plan_version_and_extra_keys():
    with pytest.raises(ValidationError):
        Plan.model_validate({"version": 2})
    with pytest.raises(ValidationError):
        Plan.model_validate({"columns": [], "bogus": 1})


def test_r2_unknown_columns_listed_with_available():
    one(mkplan(("zzz", [])), "'zzz' does not exist (available columns: a, b, c)")
    one(mkplan(("a", [{"op": "map_from_column", "source_column": "q", "mapping": {}}])), "'q' does not exist")
    one(mkplan(("a", [{"op": "validate", "pattern": "x", "message": "m"},
                      {"op": "llm_fix", "instruction": "i", "context_columns": ["q"]}])), "'q' does not exist")


def test_r2_applies_to_disabled_ops():
    one(mkplan(("a", [{"op": "map_from_column", "source_column": "q", "mapping": {}, "enabled": False}])),
        "'q' does not exist")


def test_r3_duplicate_column():
    one(mkplan(("a", []), ("a", [])), "more than once")


def test_r4_not_cleanable():
    cols = [ColumnInfo(name="n", cleanable=False)]
    one(mkplan(("n", [{"op": "trim"}])), "not cleanable", cols)


def test_r5_regexes_compile():
    one(mkplan(("a", [{"op": "regex_replace", "pattern": "("}])), "invalid regex")
    one(mkplan(("a", [{"op": "reformat", "pattern": "[", "template": ""}])), "invalid regex")
    one(mkplan(("a", [{"op": "validate", "pattern": "*", "message": "m"}])), "invalid regex")
    one(mkplan(("a", [{"op": "null_if", "pattern": "("}])), "invalid regex")
    one(mkplan(("a", [{"op": "llm_fix", "instruction": "i", "when": "if_not_match", "pattern": "("}])),
        "invalid regex")


def test_r5_replacement_group_reference_pinned():
    # CPython parses the replacement template eagerly even when nothing matches.
    with pytest.raises(re.error):
        re.compile("(a)").sub(r"\2", "")
    one(mkplan(("a", [{"op": "regex_replace", "pattern": "(a)", "replacement": r"\2"}])), "replacement")
    one(mkplan(("a", [{"op": "reformat", "pattern": "(a)", "template": r"\g<nope>"}])), "template")
    assert errs(mkplan(("a", [{"op": "regex_replace", "pattern": "(a)", "replacement": r"\1\1"}]))) == []


def test_r5_dollar_backreference_rejected():
    one(mkplan(("a", [{"op": "regex_replace", "pattern": "(a)", "replacement": "$1"}])), "$N")
    one(mkplan(("a", [{"op": "reformat", "pattern": "(a)", "template": "x$2"}])), "$N")
    assert errs(mkplan(("a", [{"op": "regex_replace", "pattern": "a", "replacement": "$"}]))) == []


def test_r5_translate_keys_single_char():
    one(mkplan(("a", [{"op": "translate", "mapping": {"ab": "x"}}])), "exactly one character")
    one(mkplan(("a", [{"op": "translate", "mapping": {"": "x"}}])), "exactly one character")


def test_r5_null_if_needs_values_or_pattern():
    one(mkplan(("a", [{"op": "null_if"}])), "'values' or 'pattern'")


def test_r5_parse_date_format():
    assert errs(mkplan(("a", [{"op": "parse_date", "output_format": "%d/%m/%Y"}]))) == []
    one(mkplan(("a", [{"op": "parse_date", "output_format": ""}])), "output_format")


def test_r5_to_r9_only_for_enabled_ops():
    plan = mkplan(("a", [{"op": "regex_replace", "pattern": "(", "enabled": False}]))
    assert errs(plan) == []


def test_r6_llm_fix_rules():
    fix = {"op": "llm_fix", "instruction": "i"}
    val = {"op": "validate", "pattern": "x", "message": "m"}
    one(mkplan(("a", [val, fix, fix])), "at most one enabled llm_fix")
    assert errs(mkplan(("a", [val, fix, {**fix, "enabled": False}]))) == []
    one(mkplan(("a", [{**fix, "when": "if_not_match"}])), "requires 'pattern'")
    one(mkplan(("a", [fix])), "requires an enabled validate or parse_date")
    one(mkplan(("a", [{**val, "enabled": False}, fix])), "requires an enabled validate or parse_date")
    one(mkplan(("a", [fix, val])), "requires an enabled validate or parse_date")  # validator must be earlier
    assert errs(mkplan(("a", [{"op": "parse_date"}, fix]))) == []
    assert errs(mkplan(("a", [{**fix, "when": "if_not_match", "pattern": "x"}]))) == []


def test_r7_llm_fix_column_cannot_be_read():
    fix = [{"op": "validate", "pattern": "x", "message": "m"}, {"op": "llm_fix", "instruction": "i"}]
    other = {"op": "map_from_column", "source_column": "a", "mapping": {}}
    one(mkplan(("a", fix), ("b", [other])), "'a' which has an llm_fix")
    one(mkplan(("b", [{"op": "validate", "pattern": "x", "message": "m"},
                      {"op": "llm_fix", "instruction": "i", "context_columns": ["a"]}]),
               ("a", fix)), "'a' which has an llm_fix")
    own = [{"op": "validate", "pattern": "x", "message": "m"},
           {"op": "llm_fix", "instruction": "i", "context_columns": ["a"]}]
    one(mkplan(("a", own)), "'a' which has an llm_fix")
    # a column without an enabled llm_fix may be read
    assert errs(mkplan(("a", [{"op": "trim"}]), ("b", [other]))) == []


def test_r8_source_must_be_planned_earlier():
    mfc = {"op": "map_from_column", "source_column": "b", "mapping": {}}
    assert errs(mkplan(("b", [{"op": "trim"}]), ("a", [mfc]))) == []
    one(mkplan(("a", [mfc]), ("b", [{"op": "trim"}])), "must appear earlier")
    assert errs(mkplan(("a", [mfc]))) == []  # unplanned source is fine
    one(mkplan(("a", [{**mfc, "source_column": "a"}])), "same column")


def test_r9_null_if_on_not_null_column():
    cols = [ColumnInfo(name="a", cleanable=True, nullable=False)]
    one(mkplan(("a", [{"op": "null_if", "values": ["x"]}])), "NOT NULL", cols)
    assert errs(mkplan(("a", [{"op": "trim"}])), cols) == []


def test_r9_in_place_key_column():
    plan = mkplan(("a", [{"op": "trim"}]))
    assert check_in_place(plan, "a") and not check_in_place(plan, "b") and not check_in_place(plan, None)


def test_warning_always():
    plan = mkplan(("a", [{"op": "llm_fix", "instruction": "i", "when": "always"}]))
    errors, warnings = check_plan(plan, COLS)
    assert errors == [] and any("always" in w for w in warnings)
