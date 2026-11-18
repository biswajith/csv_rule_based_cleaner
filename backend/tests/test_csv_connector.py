from __future__ import annotations

from pathlib import Path

import pytest
from util import read_csv, write_csv

from scrubber.connectors import CSVSource, OutputExistsError, SourceError, SQLSource, open_source
from scrubber.connectors.base import SourceSpec
from scrubber.connectors.csv_source import CSVConnector


def conn(path: Path, **kw) -> CSVConnector:
    return CSVConnector(CSVSource(path=str(path), **kw))


def test_basic_read_and_metadata(tmp_path):
    p = write_csv(tmp_path / "a.csv", [["x", "y"], ["1", "2"], ["3", "4"]])
    c = conn(p)
    assert c.description == "CSV a.csv" and c.null_equals_empty is True
    assert [(i.name, i.cleanable, i.nullable, i.max_length) for i in c.column_info()] == [
        ("x", True, True, None), ("y", True, True, None)]
    assert c.count() == 2
    assert list(c.iter_batches(10)) == [[(0, {"x": "1", "y": "2"}), (1, {"x": "3", "y": "4"})]]


def test_batches_same_rows_same_order_twice(tmp_path):
    p = write_csv(tmp_path / "a.csv", [["x"]] + [[str(i)] for i in range(7)])
    c = conn(p)
    first = list(c.iter_batches(3))
    assert [len(b) for b in first] == [3, 3, 1]
    assert first == list(c.iter_batches(3))
    assert [k for b in first for k, _ in b] == list(range(7))


def test_bom_is_stripped(tmp_path):
    p = tmp_path / "bom.csv"
    p.write_bytes(b"\xef\xbb\xbfname,age\nAnn,3\n")
    assert [i.name for i in conn(p).column_info()] == ["name", "age"]


def test_delimiter(tmp_path):
    p = write_csv(tmp_path / "s.csv", [["a", "b"], ["1", "2"]], delimiter=";")
    c = conn(p, delimiter=";")
    assert list(c.iter_batches(5))[0][0][1] == {"a": "1", "b": "2"}


def test_short_rows_padded_long_rows_kept_in_extra(tmp_path):
    p = write_csv(tmp_path / "r.csv", [["a", "b"], ["1"], ["1", "2", "3", "4"], ["x", "y"]])
    rows = [r for b in conn(p).iter_batches(10) for _, r in b]
    assert rows[0] == {"a": "1", "b": ""}
    assert rows[1] == {"a": "1", "b": "2", "__extra__": ["3", "4"]}
    assert "__extra__" not in rows[2]


def test_blank_lines_skipped_and_quoted_newlines_kept(tmp_path):
    p = tmp_path / "q.csv"
    p.write_text('a,b\n"x\ny",2\n\n3,4\n', encoding="utf-8")
    c = conn(p)
    assert c.count() == 2
    assert list(c.iter_batches(5))[0][0][1]["a"] == "x\ny"


@pytest.mark.parametrize("content,needle", [
    (b"", "no header"),
    (b"a,a\n1,2\n", "duplicate"),
    (b"a,\n1,2\n", "empty column name"),
    (b"a,b\n\xff\xfe,1\n", "--encoding"),
])
def test_connect_time_errors(tmp_path, content, needle):
    p = tmp_path / "bad.csv"
    p.write_bytes(content)
    with pytest.raises(SourceError, match=needle):
        conn(p)


def test_missing_file_and_unknown_encoding(tmp_path):
    with pytest.raises(SourceError, match="not found"):
        conn(tmp_path / "nope.csv")
    p = write_csv(tmp_path / "a.csv", [["x"], ["1"]])
    with pytest.raises(SourceError, match="encoding"):
        conn(p, encoding="not-a-codec")


def test_latin1_with_encoding_option(tmp_path):
    p = tmp_path / "l.csv"
    p.write_bytes("name\nJosé\n".encode("latin-1"))
    assert list(conn(p, encoding="latin-1").iter_batches(5))[0][0][1]["name"] == "José"


def test_sample_is_seeded_reservoir_in_file_order_and_caches_count(tmp_path):
    p = write_csv(tmp_path / "a.csv", [["x"]] + [[str(i)] for i in range(500)])
    c = conn(p)
    s1 = c.sample(20)
    assert len(s1) == 20 and s1 == conn(p).sample(20)  # deterministic
    vals = [int(r["x"]) for r in s1]
    assert vals == sorted(vals) and max(vals) > 20  # not just the first N
    assert c.count() == 500
    assert len(c.sample(1000)) == 500


def test_writer_roundtrip_with_extra_fields_and_none(tmp_path):
    p = write_csv(tmp_path / "in.csv", [["a", "b"], ["1", "x,y"], ["2", "3", "4"]])
    c = conn(p)
    run = tmp_path / "run"
    with c.writer(run_dir=run, in_place=False, overwrite=False) as w:
        batch = [(k, {**r, "a": None} if k == 0 else r, set()) for b in c.iter_batches(10) for k, r in b]
        w.write(batch)
    out = run / "in.cleaned.csv"
    assert w.target == str(out)
    assert read_csv(out) == [["a", "b"], ["", "x,y"], ["2", "3", "4"]]
    assert not out.read_bytes().startswith(b"\xef\xbb\xbf")


def test_writer_errors(tmp_path):
    c = conn(write_csv(tmp_path / "in.csv", [["a"], ["1"]]))
    with pytest.raises(SourceError, match="in-place"):
        c.writer(run_dir=tmp_path, in_place=True, overwrite=False)
    c.writer(run_dir=tmp_path / "r", in_place=False, overwrite=False).close()
    with pytest.raises(OutputExistsError):
        c.writer(run_dir=tmp_path / "r", in_place=False, overwrite=False)
    assert OutputExistsError.__mro__[1] is SourceError
    c.writer(run_dir=tmp_path / "r", in_place=False, overwrite=True).close()


def test_open_source_dispatch_and_lazy_imports(tmp_path):
    import subprocess
    import sys
    p = write_csv(tmp_path / "a.csv", [["x"], ["1"]])
    assert isinstance(open_source(CSVSource(path=str(p))), CSVConnector)
    # fresh interpreter: other test modules legitimately import the SQL/Mongo connectors
    code = ("import sys; from scrubber.connectors import CSVSource, open_source; "
            f"open_source(CSVSource(path={str(p)!r})); "
            "assert 'scrubber.connectors.sql_source' not in sys.modules; "
            "assert 'scrubber.connectors.mongo_source' not in sys.modules")
    src = str(Path(__file__).resolve().parents[1] / "src")
    subprocess.run([sys.executable, "-c", code], check=True, env={"PYTHONPATH": src})


def test_source_specs_discriminated_and_schema_alias():
    from pydantic import TypeAdapter
    ta = TypeAdapter(SourceSpec)
    s = ta.validate_python({"kind": "sql", "url": "sqlite://", "table": "t", "schema": "main"})
    assert isinstance(s, SQLSource) and s.db_schema == "main"
    assert SQLSource(url="u", table="t", db_schema="x").db_schema == "x"
    assert ta.validate_python({"kind": "mongo", "uri": "u", "database": "d", "collection": "c"}).kind == "mongo"
