from __future__ import annotations

import json

import pytest
from util import write_csv

from scrubber import planner
from scrubber.config import get_settings
from scrubber.connectors import CSVSource, open_source
from scrubber.llm import FakeLLM, LLMError
from scrubber.planner import PlanError, plan, preview
from scrubber.plan import Plan

GOOD = {
    "columns": [{"column": "phone", "rationale": "r", "ops": [
        {"op": "regex_replace", "pattern": "[^0-9]", "replacement": ""},
        {"op": "reformat", "pattern": r"(\d{3})(\d{4})", "template": r"\1-\2"},
        {"op": "validate", "pattern": r"\d{3}-\d{4}", "message": "bad phone"}]}],
    "notes": ["dedupe not applied"],
}
BAD_REF = {"columns": [{"column": "nope", "ops": []}], "notes": []}


@pytest.fixture
def conn(tmp_path):
    rows = [["name", "phone"], ["a", "555-1234"], ["b", "5551234"], ["c", "55512"], ["d", "x"], ["e", ""]]
    return open_source(CSVSource(path=str(write_csv(tmp_path / "p.csv", rows))))


def test_plan_happy_path(conn):
    llm = FakeLLM([GOOD])
    res = plan(conn, ["Phones look like 555-1234", "  ", "Remove duplicates"], llm)
    assert res.plan.version == 1 and res.plan.rules == ["Phones look like 555-1234", "Remove duplicates"]
    assert res.plan.notes == ["dedupe not applied"] and res.usage["calls"] == 1
    (messages, schema), = llm.calls
    assert schema == "RawJSON" and messages[0][0] == "system" and "JSON" in messages[0][1]
    user = messages[1][1]
    assert "1. Phones look like 555-1234" in user and "2. Remove duplicates" in user
    prof = json.loads(user.split("Column profile (JSON data):\n")[1])
    assert [c["name"] for c in prof["columns"]] == ["name", "phone"] and prof["sampled_rows"] == 5
    assert res.preview.columns[0].cells_changed == 2 and res.preview.sampled_rows == 5


def test_rules_required(conn):
    with pytest.raises(ValueError, match="rule"):
        plan(conn, [" ", ""], FakeLLM([]))
    with pytest.raises(ValueError, match="rule"):
        plan(conn, [], FakeLLM([]))


def test_columns_option_limits_profile_and_rejects_unknown(conn):
    llm = FakeLLM([GOOD])
    plan(conn, ["r"], llm, columns=["phone"])
    prof = json.loads(llm.calls[0][0][1][1].split("Column profile (JSON data):\n")[1])
    assert [c["name"] for c in prof["columns"]] == ["phone"]
    with pytest.raises(ValueError, match="unknown column"):
        plan(conn, ["r"], FakeLLM([]), columns=["zzz"])


def test_invalid_json_then_repair(conn):
    llm = FakeLLM(["nonsense", GOOD])
    assert plan(conn, ["r"], llm).usage["calls"] == 2


def test_schema_invalid_plan_then_repair(conn):
    bad = {"columns": [{"column": "phone", "ops": [{"op": "trim", "bogus": 1}]}], "notes": []}
    llm = FakeLLM([bad, GOOD])
    plan(conn, ["r"], llm)
    assert "bogus" in llm.calls[1][0][-1][1]


def test_check_plan_errors_drive_repair_conversation(conn):
    llm = FakeLLM([BAD_REF, GOOD])
    res = plan(conn, ["r"], llm)
    assert res.plan.columns[0].column == "phone"
    msgs = llm.calls[1][0]
    assert msgs[-2] == ("ai", json.dumps(BAD_REF))
    assert msgs[-1][0] == "human" and "'nope' does not exist" in msgs[-1][1] and "available columns" in msgs[-1][1]
    assert len(llm.calls[1][0]) == len(llm.calls[0][0]) + 2


def test_gives_up_after_max_repairs(conn):
    llm = FakeLLM([BAD_REF, BAD_REF, BAD_REF, GOOD])
    with pytest.raises(PlanError) as ei:
        plan(conn, ["r"], llm)
    assert len(llm.calls) == 3  # initial + PLANNER_MAX_REPAIRS (2)
    assert any("'nope'" in e for e in ei.value.errors)


def test_llm_error_propagates(conn):
    with pytest.raises(LLMError):
        plan(conn, ["r"], FakeLLM(["x", "y"]))


def test_profile_size_degradation(tmp_path):
    header = ["c%d" % i for i in range(6)]
    rows = [header] + [[("v%d_%d" % (i, j)) for j in range(6)] for i in range(40)]
    c = open_source(CSVSource(path=str(write_csv(tmp_path / "w.csv", rows))))
    prof = planner.profile(c)
    full = planner._profile_json(prof, 10**9)
    reduced = planner._profile_json(prof, len(full) - 1)
    r = json.loads(reduced)
    assert all(len(col["top_values"]) == 5 and len(col["rare_examples"]) <= 5 for col in r["columns"])
    assert all(col["all_values"] is not None for col in r["columns"])
    no_all = planner._profile_json(prof, len(reduced) - 1)
    assert all(col["all_values"] is None for col in json.loads(no_all)["columns"])
    assert len(no_all) < len(reduced)
    with pytest.raises(ValueError, match="pass --columns"):
        planner._profile_json(prof, 100)


def test_profile_size_limit_applied_in_plan(conn, monkeypatch):
    monkeypatch.setenv("PLANNER_MAX_PROFILE_CHARS", "50")
    get_settings.cache_clear()
    with pytest.raises(ValueError, match="Too many columns"):
        plan(conn, ["r"], FakeLLM([GOOD]))


def test_dry_run_counts_examples_and_warnings(conn):
    p = Plan.model_validate({**GOOD, "columns": [{"column": "phone", "ops": [
        {"op": "regex_replace", "pattern": "[^0-9]"},
        {"op": "reformat", "pattern": r"(\d{3})(\d{4})", "template": r"\1-\2"},
        {"op": "validate", "pattern": r"\d{3}-\d{4}", "message": "bad phone"},
        {"op": "trim"},
        {"op": "llm_fix", "instruction": "i", "context_columns": ["name"]}]}]})
    pv = preview(conn, p)
    col = pv.columns[0]
    assert pv.sampled_rows == 5 and pv.total_rows == 5
    # "555-1234" and "5551234" end equal; "x" -> ""; "55512" is flagged but unchanged
    assert (col.cells_changed, col.cells_flagged) == (2, 1)
    by_op = {o.op_index: (o.op, o.changed, o.flagged) for o in col.ops}
    assert by_op == {0: ("regex_replace", 2, 0), 1: ("reformat", 2, 0), 2: ("validate", 0, 1),
                     3: ("trim", 0, 0), 4: ("llm_fix", 0, 0)}
    assert col.examples == [("5551234", "555-1234"), ("x", "")]
    assert col.flag_examples == [("55512", "bad phone")]
    assert (col.llm_cells, col.llm_unique, pv.llm_cells_sample, pv.llm_cells_estimate) == (1, 1, 1, 1)
    assert not any("op 3 (trim)" in w for w in pv.warnings)  # trim never warns (C4)
    assert not any("op 0" in w for w in pv.warnings)


def test_dry_run_estimate_is_upper_bound_scaling(conn):
    p = Plan.model_validate({"columns": [{"column": "phone", "ops": [
        {"op": "llm_fix", "instruction": "i", "when": "if_not_match", "pattern": r"\d+"}]}]})
    rows = [{"phone": "a"}, {"phone": "a"}, {"phone": "1"}, {"phone": "b"}]
    from scrubber.connectors.base import ColumnInfo
    pv = planner.dry_run(p, rows, 400, [ColumnInfo(name="phone", cleanable=True)], True)
    assert (pv.llm_cells_sample, pv.llm_unique_sample, pv.llm_cells_estimate) == (3, 2, 300)
    assert planner.dry_run(p, rows, None, [ColumnInfo(name="phone", cleanable=True)], True).llm_cells_estimate is None


def test_examples_are_distinct_and_capped():
    from scrubber.connectors.base import ColumnInfo
    p = Plan.model_validate({"columns": [{"column": "c", "ops": [{"op": "case", "mode": "upper"},
                                                                  {"op": "validate", "pattern": "Z", "message": "m"}]}]})
    rows = [{"c": "a%d" % (i % 9)} for i in range(50)]
    pv = planner.dry_run(p, rows, 50, [ColumnInfo(name="c", cleanable=True)], False)
    col = pv.columns[0]
    assert len(col.examples) == 5 and len(set(col.examples)) == 5
    assert len(col.flag_examples) == 5 and col.cells_flagged == 50
    assert col.examples[0] == ("a0", "A0") and col.flag_examples[0] == ("A0", "m")


def test_preview_validates_plan_first(conn):
    with pytest.raises(PlanError):
        preview(conn, Plan.model_validate(BAD_REF))
