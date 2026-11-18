from __future__ import annotations

import pytest
from util import infos, mkplan

from scrubber.ops import apply_row, compile_regex, is_empty, stringify


def run(ops, value, row=None, col="c", **kw):
    """Apply ops to one cell; returns (final value, flag messages)."""
    r = {col: value, **(row or {})}
    res = apply_row(mkplan((col, ops)), r, **kw)
    return res.values[col], [f.message for f in res.flags]


def test_helpers():
    assert is_empty(None) and is_empty("") and is_empty("  \t") and not is_empty("a") and not is_empty(0)
    assert stringify(None) == "" and stringify(" a ") == "a" and stringify(5) == "5"


def test_trim_and_collapse():
    assert run([{"op": "trim"}], "  a  b ")[0] == "a  b"
    assert run([{"op": "collapse_whitespace"}], " a \t\n b ")[0] == " a b "


@pytest.mark.parametrize("mode,inp,out", [
    ("lower", "AbC", "abc"), ("upper", "AbC", "ABC"),
    ("title", "o'brien", "O'brien"), ("title", "mcdonald's", "Mcdonald's"),
    ("title", "IBM corp", "Ibm Corp"), ("title", "jean-luc PICARD", "Jean-Luc Picard"),
    ("title", "élan 3rd", "Élan 3Rd"),
])
def test_case(mode, inp, out):
    assert run([{"op": "case", "mode": mode}], inp)[0] == out


def test_regex_replace():
    assert run([{"op": "regex_replace", "pattern": "[^0-9]"}], "a1b2")[0] == "12"
    assert run([{"op": "regex_replace", "pattern": "x", "replacement": "y"}], "XxX")[0] == "XyX"
    assert run([{"op": "regex_replace", "pattern": "x", "replacement": "y", "ignore_case": True}], "XxX")[0] == "yyy"
    assert run([{"op": "regex_replace", "pattern": "@{2,}", "replacement": "@"}], "a@@@b")[0] == "a@b"


def test_reformat_full_match_only():
    op = {"op": "reformat", "pattern": r"(\d{3})(\d{4})", "template": r"\1-\2"}
    assert run([op], "5551234")[0] == "555-1234"
    assert run([op], "x5551234")[0] == "x5551234"  # partial match: unchanged, no flag
    assert run([op], " 5551234")[0] == " 5551234"  # no implicit strip
    assert run([{**op, "pattern": r"(?P<a>[a-z]+)", "template": r"\g<a>!", "ignore_case": True}], "AbC")[0] == "AbC!"


def test_translate():
    op = {"op": "translate", "mapping": {"A": "2", "B": "2", "-": ""}}
    assert run([op], "5A-B")[0] == "522"
    assert run([op], "a")[0] == "a"  # ignore_case=false by default
    assert run([{**op, "ignore_case": True}], "ab")[0] == "22"
    # exact key wins over the folded lookup; upper tried before lower
    assert run([{"op": "translate", "mapping": {"a": "x", "A": "y"}, "ignore_case": True}], "aA")[0] == "xy"
    assert run([{"op": "translate", "mapping": {"a": "1"}, "ignore_case": True}], "A")[0] == "1"


def test_map_values():
    op = {"op": "map_values", "mapping": {"California": "CA", "NY": "New York"}}
    assert run([op], " california ")[0] == "CA"
    assert run([op], "Texas")[0] == "Texas"
    assert run([{**op, "ignore_case": False}], "california")[0] == "california"
    # exact-case key wins; among folded collisions first key wins
    coll = {"op": "map_values", "mapping": {"ab": "1", "AB": "2", "Ab": "3"}}
    assert run([coll], "AB")[0] == "2"
    assert run([coll], "aB")[0] == "1"


MFC = {"op": "map_from_column", "source_column": "city", "mapping": {"Miami": "FL", "Austin": "TX"}}


def test_map_from_column_overwrite():
    assert run([MFC], "CA", {"city": "miami"})[0] == "FL"
    assert run([MFC], "", {"city": "Austin"})[0] == "TX"
    assert run([MFC], None, {"city": "Austin"})[0] == "TX"
    assert run([MFC], "CA", {"city": "Denver"})[0] == "CA"  # no hit
    assert run([MFC], "CA", {"city": ""})[0] == "CA"  # empty source
    assert run([MFC], " fl ", {"city": "Miami"})[0] == " fl "  # equal after strip+casefold: untouched


def test_map_from_column_fill_empty_conflict_flag():
    op = {**MFC, "mode": "fill_empty"}
    assert run([op], "", {"city": "Miami"}) == ("FL", [])
    assert run([op], "fl", {"city": "Miami"}) == ("fl", [])
    v, flags = run([op], "CA", {"city": "Miami"})
    assert v == "CA"
    assert flags == ["c 'CA' conflicts with city 'Miami' (expected 'FL')"]


def test_map_from_column_non_str_target_noop_and_stringified_source():
    assert run([MFC], 5, {"city": "Miami"})[0] == 5
    op = {**MFC, "mapping": {"42": "x"}}
    assert run([op], "", {"city": 42})[0] == "x"


def test_map_from_column_reads_cleaned_value_if_planned_earlier():
    plan = mkplan(("city", [{"op": "trim"}, {"op": "case", "mode": "title"}]),
                  ("c", [MFC]))
    assert apply_row(plan, {"city": "  miami ", "c": ""}).values["c"] == "FL"
    cs = {**MFC, "ignore_case": False}
    plan = mkplan(("c", [cs]), ("city", [{"op": "case", "mode": "title"}]))
    assert apply_row(plan, {"city": "miami", "c": ""}).values["c"] == ""  # raw 'miami' is cleaned later


def test_null_if():
    op = {"op": "null_if", "values": ["N/A", "null"], "pattern": r"-+|none"}
    assert run([op], " n/a ")[0] is None
    assert run([op], "NULL")[0] is None
    assert run([op], "---")[0] is None
    assert run([op], " NONE ")[0] is None
    assert run([op], "n/a x")[0] == "n/a x"
    assert run([op], "")[0] == ""


@pytest.mark.parametrize("inp,out", [
    ("2024-03-05", "2024-03-05"), ("March 5, 2024", "2024-03-05"), ("5/3/2024", "2024-05-03"),
])
def test_parse_date(inp, out):
    assert run([{"op": "parse_date"}], inp) == (out, [])


def test_parse_date_day_first_and_format():
    assert run([{"op": "parse_date", "day_first": True}], "5/3/2024")[0] == "2024-03-05"
    assert run([{"op": "parse_date", "output_format": "%d.%m.%Y"}], "2024-03-05")[0] == "05.03.2024"


@pytest.mark.parametrize("inp", ["May", "12", "10:30", "garbage", "13/45/2020"])
def test_parse_date_partial_or_bad_flagged(inp):
    assert run([{"op": "parse_date"}], inp) == (inp, ["Unparseable date"])


def test_parse_date_empty_and_non_str_untouched():
    assert run([{"op": "parse_date"}], "  ") == ("  ", [])
    assert run([{"op": "parse_date"}], None) == (None, [])
    assert run([{"op": "parse_date"}], 20240305) == (20240305, [])


VAL = {"op": "validate", "pattern": r"[A-Z]{2}", "message": "bad"}


def test_validate():
    assert run([VAL], "CA") == ("CA", [])
    assert run([VAL], "ca") == ("ca", ["bad"])
    assert run([VAL], "CAx") == ("CAx", ["bad"])  # full match only
    assert run([{**VAL, "ignore_case": True}], "ca") == ("ca", [])
    assert run([VAL], "") == ("", [])  # allow_empty default
    assert run([VAL], None) == (None, [])
    assert run([{**VAL, "allow_empty": False}], "  ") == ("  ", ["bad"])
    assert run([{**VAL, "allow_empty": False}], None) == (None, ["bad"])


def test_string_only_rule():
    for v in (5, 1.5, True, b"x", ["a"], {"a": 1}):
        out, flags = run([{"op": "trim"}, {"op": "case", "mode": "upper"},
                          {"op": "regex_replace", "pattern": "."}, VAL,
                          {"op": "null_if", "values": ["5"]}], v)
        assert out == v and flags == []


def test_none_passes_through_other_ops():
    assert run([{"op": "trim"}, {"op": "case", "mode": "upper"}, {"op": "null_if", "values": ["x"]}], None)[0] is None


def test_ops_see_previous_output_and_disabled_keep_index():
    plan = mkplan(("c", [{"op": "trim"}, {"op": "case", "mode": "upper", "enabled": False},
                         {"op": "validate", "pattern": "[a-z]+", "message": "m"}]))
    res = apply_row(plan, {"c": " ab "})
    assert res.values["c"] == "ab" and res.flags == []
    assert res.op_stats[("c", 0)] == [1, 0]
    assert ("c", 1) not in res.op_stats and ("c", 2) in res.op_stats


def test_flags_not_cleared_by_later_ops():
    plan = mkplan(("c", [VAL, {"op": "case", "mode": "upper"}]))
    res = apply_row(plan, {"c": "ca"})
    assert res.values["c"] == "CA"
    assert [(f.op_index, f.message, f.value) for f in res.flags] == [(0, "bad", "CA")]  # value = final


def test_changed_sources_and_cancelling_ops():
    plan = mkplan(("c", [{"op": "trim"}, {"op": "case", "mode": "lower"}, {"op": "case", "mode": "upper"}]))
    res = apply_row(plan, {"c": " Ab "})
    assert res.changed["c"] == (" Ab ", "AB", ["trim", "case"])
    plan = mkplan(("c", [{"op": "case", "mode": "lower"}, {"op": "case", "mode": "upper"}]))
    assert apply_row(plan, {"c": "AB"}).changed == {}


def test_null_equals_empty_diffing():
    plan = mkplan(("c", [{"op": "null_if", "values": ["n/a"]}]))
    res = apply_row(plan, {"c": "n/a"}, null_equals_empty=True)
    assert res.changed["c"][1] is None
    plan = mkplan(("c", [{"op": "null_if", "pattern": r"\s*"}]))
    assert apply_row(plan, {"c": ""}, null_equals_empty=True).changed == {}
    assert "c" in apply_row(plan, {"c": ""}, null_equals_empty=False).changed


def test_max_length_revert_and_flag():
    plan = mkplan(("c", [{"op": "regex_replace", "pattern": "a", "replacement": "aaaa"}]))
    ci = {"c": infos("c", max_length=5)[0]}
    res = apply_row(plan, {"c": "aa"}, columns=ci)
    assert res.values["c"] == "aa" and res.changed == {}
    assert [(f.column, f.op_index, f.message) for f in res.flags] == [("c", None, "Result exceeds column length (5)")]
    assert apply_row(plan, {"c": "a"}, columns=ci).values["c"] == "aaaa"


def test_extra_field_row_flag_and_passthrough():
    plan = mkplan(("c", [{"op": "trim"}]))
    res = apply_row(plan, {"c": " a", "__extra__": ["x", "y"]})
    assert res.values["__extra__"] == ["x", "y"]
    assert [(f.column, f.op_index, f.message) for f in res.flags] == [("", None, "Row has 2 extra field(s)")]


def test_input_row_not_mutated():
    row = {"c": " a "}
    apply_row(mkplan(("c", [{"op": "trim"}])), row)
    assert row == {"c": " a "}


def test_regex_cache():
    assert compile_regex("a", False) is compile_regex("a", False)
    assert compile_regex("a", True) is not compile_regex("a", False)
