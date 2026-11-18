from __future__ import annotations

from datetime import date

from util import infos

from scrubber.profiler import profile_rows, shape_of


def test_shape_preserves_digit_counts():
    assert shape_of("(555) 123-4567") == "(999) 999-9999"
    assert shape_of("John.Doe@x.com") == "A.A@A.A"
    assert shape_of("5551234") != shape_of("555123")
    assert shape_of("a  b\t c") == "A A A"
    assert shape_of("O'Brien 3rd") == "A'A 9A"
    assert shape_of("x" * 300) == "A"  # computed on the first 120 chars


def col(values, name="c", **kw):
    return profile_rows([{name: v} for v in values], infos(name, **kw), "src", len(values)).columns[0]


def test_counts_and_top_values():
    p = col(["a", "a", "b", None, "", "  ", "c", "a", "b"])
    assert (p.non_empty, p.empty, p.distinct_count) == (6, 3, 3)
    assert p.top_values == [("a", 3), ("b", 2), ("c", 1)]
    assert p.inferred_type == "string"


def test_top_values_capped_at_10_and_shapes_at_12_with_rare_examples():
    vals = ["v%d" % i for i in range(30)]
    p = col(vals)
    assert len(p.top_values) == 10
    vals = ["1" * n + "a" for n in range(1, 31)]  # 30 distinct shapes
    p = col(vals)
    assert len(p.shapes) == 12 and len(p.rare_examples) == 15
    assert all(s.example for s in p.shapes)
    shown = {s.shape for s in p.shapes}
    assert all(shape_of(r) not in shown for r in p.rare_examples)


def test_inferred_types():
    assert col(["1", "2"]).inferred_type == "int"
    assert col(["1.5", "2"]).inferred_type == "float"
    assert col([1, 2]).inferred_type == "int"
    assert col([True, False]).inferred_type == "bool"
    assert col([date(2020, 1, 1)]).inferred_type == "date"
    assert col(["2020-01-01"]).inferred_type == "date"
    assert col(["a", "1"]).inferred_type == "mixed"
    assert col([None, ""]).inferred_type == "empty"
    assert col([["x"]]).inferred_type == "other"


def test_stats_over_stringified_values():
    p = col([1, 1, "1", 2])
    assert p.top_values[0] == ("1", 3)


def test_all_values_thresholds():
    assert col(["b", "a", "b"]).all_values == ["a", "b"]  # sorted distinct
    assert col(["v%d" % i for i in range(60)]).all_values is not None
    assert col(["v%d" % i for i in range(61)]).all_values is None
    assert col(["x" * 100 + str(i) for i in range(20)]).all_values is None  # > 2000 chars total
    assert col(["x" * 100 + str(i) for i in range(19)]).all_values is not None


def test_truncation_to_120_chars():
    p = col(["y" * 500, "y" * 500])
    assert p.top_values[0][0] == "y" * 120 + "…"
    assert p.shapes[0].example == "y" * 120 + "…"


def test_profile_metadata_and_non_cleanable():
    prof = profile_rows([{"c": "a"}], [infos("c")[0].model_copy(update={"cleanable": False})], "desc", None)
    assert prof.source == "desc" and prof.total_rows is None and prof.sampled_rows == 1
    assert prof.columns[0].cleanable is False
