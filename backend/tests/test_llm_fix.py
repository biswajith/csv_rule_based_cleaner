from __future__ import annotations

from util import infos, mkplan

from scrubber.ops import Answer, LLMKey, apply_row

VAL = {"op": "validate", "pattern": r"[a-z]+@[a-z]+\.com", "message": "Invalid email"}
FIX = {"op": "llm_fix", "instruction": "fix email", "context_columns": ["name"]}
PLAN = mkplan(("email", [{"op": "trim"}, VAL, FIX, {"op": "case", "mode": "upper"}]))


def answer(value, fixed=True, reason="", failure=None):
    return lambda key: Answer(value, fixed, reason, failure)


def run(plan, row, resolver=None, **kw):
    return apply_row(plan, row, resolver, **kw)


def test_no_resolver_records_key_value_unchanged_no_flag_for_llm():
    res = run(PLAN, {"email": " bad ", "name": " Ann "})
    assert res.values["email"] == "BAD"  # later ops still ran on the unchanged value
    assert res.llm_keys == [LLMKey("email", 2, "bad", (("name", "Ann"),))]
    assert [f.message for f in res.flags] == ["Invalid email"]


def test_if_flagged_not_triggered_when_valid_or_empty_or_non_str():
    assert run(PLAN, {"email": "a@b.com", "name": ""}).llm_keys == []
    assert run(PLAN, {"email": "  ", "name": ""}).llm_keys == []
    assert run(PLAN, {"email": None, "name": ""}).llm_keys == []
    assert run(PLAN, {"email": 5, "name": ""}).llm_keys == []


def test_if_flagged_ignores_map_from_column_conflict_flag():
    plan = mkplan(("s", [{"op": "map_from_column", "source_column": "city", "mapping": {"Miami": "FL"},
                          "mode": "fill_empty"},
                         {"op": "validate", "pattern": r"[A-Z]{2}", "message": "m"},
                         {"op": "llm_fix", "instruction": "i"}]))
    res = run(plan, {"s": "CA", "city": "Miami"})  # conflict flag only; validate passes
    assert len(res.flags) == 1 and res.llm_keys == []


def test_if_not_match_trigger():
    plan = mkplan(("c", [{"op": "llm_fix", "instruction": "i", "when": "if_not_match", "pattern": r"\d+"}]))
    assert run(plan, {"c": "123"}).llm_keys == []
    assert [k.value for k in run(plan, {"c": "12a"}).llm_keys] == ["12a"]
    assert [k.value for k in run(plan, {"c": "12 "}).llm_keys] == ["12 "]  # case/whitespace exact, full match


def test_always_trigger():
    plan = mkplan(("c", [{"op": "llm_fix", "instruction": "i", "when": "always"}]))
    assert len(run(plan, {"c": "x"}).llm_keys) == 1
    assert run(plan, {"c": ""}).llm_keys == []


def test_accepted_clears_validate_flags_and_later_ops_run():
    res = run(PLAN, {"email": "bad", "name": "Ann"}, answer("ann@x.com"))
    assert res.values["email"] == "ANN@X.COM"  # later case op ran on the accepted value
    assert res.flags == []
    assert res.changed["email"] == ("bad", "ANN@X.COM", ["llm_fix", "case"])
    assert res.op_stats[("email", 1)] == [0, 0] and res.op_stats[("email", 2)] == [1, 0]


def test_accepted_keeps_flags_of_other_kinds():
    plan = mkplan(("c", [{"op": "validate", "pattern": r"\d+", "message": "m"},
                         {"op": "llm_fix", "instruction": "i"},
                         ]))
    res = run(plan, {"c": "x"}, answer("12"))
    assert res.values["c"] == "12" and res.flags == []


def test_rejected_by_validator_keeps_value_adds_flag():
    res = run(PLAN, {"email": "bad", "name": ""}, answer("still bad"))
    assert res.values["email"] == "BAD"
    assert [f.message for f in res.flags] == ["Invalid email", "LLM fix rejected: result failed validation"]


def test_candidate_goes_through_parse_date():
    plan = mkplan(("d", [{"op": "parse_date"}, {"op": "llm_fix", "instruction": "i"}]))
    res = run(plan, {"d": "someday"}, answer("March 5, 2024"))
    assert res.values["d"] == "2024-03-05" and res.flags == []
    res = run(plan, {"d": "someday"}, answer("nonsense"))
    assert res.values["d"] == "someday" and res.flags[-1].message == "LLM fix rejected: result failed validation"


def test_if_not_match_candidate_must_match_pattern():
    plan = mkplan(("c", [{"op": "llm_fix", "instruction": "i", "when": "if_not_match", "pattern": r"\d+"}]))
    assert run(plan, {"c": "x"}, answer("12")).values["c"] == "12"
    res = run(plan, {"c": "x"}, answer("zz"))
    assert res.values["c"] == "x" and res.flags[0].message == "LLM fix rejected: result failed validation"


def test_always_without_validators_accepts_anything():
    plan = mkplan(("c", [{"op": "llm_fix", "instruction": "i", "when": "always"}]))
    assert run(plan, {"c": "x"}, answer("whatever")).values["c"] == "whatever"


def test_only_validate_and_parse_date_are_rerun_on_candidate():
    plan = mkplan(("c", [{"op": "case", "mode": "upper"}, {"op": "validate", "pattern": "[A-Z]+", "message": "m"},
                         {"op": "llm_fix", "instruction": "i"}]))
    # candidate is lowercase: the case op is NOT re-run, so validation fails
    assert run(plan, {"c": "x1"}, answer("abc")).flags[-1].message == "LLM fix rejected: result failed validation"
    assert run(plan, {"c": "x1"}, answer("ABC")).values["c"] == "ABC"


def test_max_length_rejects_candidate():
    ci = {"email": infos("email", max_length=8)[0]}
    res = run(PLAN, {"email": "bad", "name": ""}, answer("long@example.com"), columns=ci)
    assert res.values["email"] == "BAD" and res.flags[-1].message == "LLM fix rejected: result failed validation"


def test_not_fixed_variants():
    for ans in (Answer(None, True), Answer("", True), Answer("bad", True), Answer("a@b.com", False, "unsure")):
        res = run(PLAN, {"email": "bad", "name": ""}, lambda k, a=ans: a)
        assert res.values["email"] == "BAD"
        assert res.flags[-1].message.startswith("LLM could not fix: ")
    res = run(PLAN, {"email": "bad", "name": ""}, answer(None, False, "cannot tell"))
    assert res.flags[-1].message == "LLM could not fix: cannot tell"


def test_failure_messages_and_missing_answer():
    res = run(PLAN, {"email": "bad", "name": ""}, answer(None, False, failure="LLM budget exceeded"))
    assert res.flags[-1].message == "LLM budget exceeded" and res.values["email"] == "BAD"
    res = run(PLAN, {"email": "bad", "name": ""}, lambda k: None)
    assert res.flags[-1].message == "LLM fix not attempted (source changed during run)"


def test_too_long_value_flagged_not_sent():
    res = run(PLAN, {"email": "x" * 50, "name": ""}, llm_max_chars=10)
    assert res.llm_keys == [] and res.flags[-1].message == "Too long for LLM fix"
    # valid long values are not flagged at all
    plan = mkplan(("c", [{"op": "llm_fix", "instruction": "i", "when": "if_not_match", "pattern": "x+"}]))
    assert run(plan, {"c": "x" * 50}, llm_max_chars=10).flags == []


def test_key_includes_stringified_context_and_dedups():
    plan = mkplan(("email", [VAL, {"op": "llm_fix", "instruction": "i", "context_columns": ["n", "m"]}]))
    k = run(plan, {"email": "x", "n": 5, "m": None}).llm_keys[0]
    assert k.context == (("n", "5"), ("m", ""))
    assert len({k, run(plan, {"email": "x", "n": " 5 ", "m": ""}).llm_keys[0]}) == 1
    assert run(plan, {"email": "x", "n": 6, "m": ""}).llm_keys[0] != k
