from __future__ import annotations

import sqlite3

import pytest
import sqlalchemy as sa
from util import mkplan

from scrubber.cleaner import clean
from scrubber.connectors import OutputExistsError, SourceError, SQLSource, open_source
from scrubber.planner import PlanError

TRIM = {"op": "trim"}


def make_db(tmp_path, ddl="CREATE TABLE people (id INTEGER PRIMARY KEY, name VARCHAR(8), "
            "email TEXT, age INTEGER, note TEXT NOT NULL DEFAULT '')", rows=None, name="t.db"):
    path = tmp_path / name
    con = sqlite3.connect(path)
    con.execute(ddl)
    rows = rows if rows is not None else [
        (1, " ann ", " A@X.com ", 30, "n1"), (2, "bob", "b@x.com", 40, "n2"),
        (3, " carol ", None, 50, "n3"), (4, "dan", " d@x.com", 60, "n4")]
    if rows:
        con.executemany(f"INSERT INTO people VALUES ({','.join('?' * len(rows[0]))})", rows)
    con.commit()
    con.close()
    return f"sqlite:///{path}"


def conn(url, **kw):
    return open_source(SQLSource(url=url, table="people", **kw))


def rows(url, table="people"):
    con = sqlite3.connect(url.removeprefix("sqlite:///"))
    try:
        return con.execute(f"SELECT * FROM {table} ORDER BY id").fetchall()
    finally:
        con.close()


def test_column_info_and_description(tmp_path):
    url = make_db(tmp_path)
    c = conn(url)
    info = {i.name: i for i in c.column_info()}
    assert info["name"].cleanable and info["name"].max_length == 8
    assert info["email"].cleanable and info["email"].max_length is None
    assert not info["age"].cleanable and not info["id"].cleanable
    assert info["note"].nullable is False and info["email"].nullable
    assert c.key_column == "id" and c.count() == 4
    assert c.description.startswith("SQL sqlite:///") and c.description.endswith("/ people")
    c.close()


def test_password_hidden(tmp_path):
    with pytest.raises(SourceError) as e:
        open_source(SQLSource(url="postgresql://u:hunter2@127.0.0.1:1/db", table="t"))
    assert "hunter2" not in str(e.value)
    with pytest.raises(SourceError) as e:
        open_source(SQLSource(url="not a url hunter2", table="t"))
    assert "hunter2" not in str(e.value)


def test_missing_table(tmp_path):
    with pytest.raises(SourceError, match="not found"):
        open_source(SQLSource(url=make_db(tmp_path), table="nope"))


def test_no_key_error(tmp_path):
    url = make_db(tmp_path, "CREATE TABLE people (a TEXT, b TEXT)", [])
    with pytest.raises(SourceError, match="single-column primary key"):
        conn(url)


def test_key_column_must_be_unique_and_non_null(tmp_path):
    url = make_db(tmp_path, "CREATE TABLE people (a TEXT, b TEXT)", [("x", "1"), ("x", "2")])
    with pytest.raises(SourceError, match="unique"):
        conn(url, key_column="a")
    url = make_db(tmp_path, "CREATE TABLE people (a TEXT, b TEXT)", [("x", "1"), (None, "2")],
                  name="n.db")
    with pytest.raises(SourceError, match="non-null"):
        conn(url, key_column="a")
    url = make_db(tmp_path, "CREATE TABLE people (a TEXT, b TEXT)", [("x", "1"), ("y", "2")],
                  name="ok.db")
    assert conn(url, key_column="a").key_column == "a"
    with pytest.raises(SourceError, match="not in table"):
        conn(url, key_column="zzz")


def test_keyset_batches_stable_order_and_sample(tmp_path):
    url = make_db(tmp_path, rows=[(i, f"n{i}", "e", i, "") for i in (5, 1, 4, 2, 3)])
    c = conn(url)
    for _ in range(2):  # same rows, same order every call
        batches = list(c.iter_batches(2))
        assert [[k for k, _ in b] for b in batches] == [[1, 2], [3, 4], [5]]
    assert [r["id"] for r in c.sample(3)] == [1, 2, 3]
    # exact multiple of batch size ends cleanly
    assert [len(b) for b in c.iter_batches(5)] == [5]


def test_new_table_writer(tmp_path):
    url = make_db(tmp_path)
    c = conn(url)
    rep = clean(c, mkplan(("email", [TRIM, {"op": "case", "mode": "lower"}])), None,
                run_dir=tmp_path / "run")
    assert rep.status == "done" and rep.target == "table people_cleaned"
    assert rows(url, "people_cleaned") == [(1, " ann ", "a@x.com", 30, "n1"),
                                           (2, "bob", "b@x.com", 40, "n2"),
                                           (3, " carol ", None, 50, "n3"),
                                           (4, "dan", "d@x.com", 60, "n4")]
    assert rows(url)[0][2] == " A@X.com "  # source untouched
    insp = sa.inspect(sa.create_engine(url))
    assert insp.get_pk_constraint("people_cleaned")["constrained_columns"] == ["id"]
    # key is not a planned column in new-table mode
    c.close()


def test_existing_target_error_then_overwrite(tmp_path):
    url = make_db(tmp_path)
    plan = mkplan(("email", [TRIM]))
    clean(conn(url), plan, None, run_dir=tmp_path / "r1")
    with pytest.raises(OutputExistsError):
        clean(conn(url), plan, None, run_dir=tmp_path / "r2")
    with pytest.raises(OutputExistsError):
        conn(url).check_target(in_place=False, overwrite=False)
    rep = clean(conn(url), plan, None, overwrite=True, run_dir=tmp_path / "r3")
    assert rep.status == "done" and len(rows(url, "people_cleaned")) == 4


def test_in_place_updates_only_changed_columns(tmp_path):
    url = make_db(tmp_path)
    c = conn(url)
    rep = clean(c, mkplan(("name", [TRIM]), ("email", [TRIM])), None, in_place=True,
                run_dir=tmp_path / "run")
    assert rep.status == "done" and rep.in_place and rep.cells_changed == 4
    assert rows(url) == [(1, "ann", "A@X.com", 30, "n1"), (2, "bob", "b@x.com", 40, "n2"),
                         (3, "carol", None, 50, "n3"), (4, "dan", "d@x.com", 60, "n4")]
    # re-running is a no-op
    rep2 = clean(c, mkplan(("name", [TRIM])), None, in_place=True, run_dir=tmp_path / "run2")
    assert rep2.cells_changed == 0


def test_in_place_key_column_cannot_be_planned(tmp_path):
    url = make_db(tmp_path, "CREATE TABLE people (id VARCHAR(5) PRIMARY KEY, v TEXT)",
                  [("a", "x")])
    with pytest.raises(PlanError, match="key column"):
        clean(conn(url), mkplan(("id", [TRIM])), None, in_place=True, run_dir=tmp_path / "r")


def test_non_string_column_not_plannable(tmp_path):
    with pytest.raises(PlanError):
        clean(conn(make_db(tmp_path)), mkplan(("age", [TRIM])), None, run_dir=tmp_path / "r")


def test_max_length_revert_and_flag(tmp_path):
    url = make_db(tmp_path, rows=[(1, "ab", "e", 1, ""), (2, "x", "e", 1, "")])
    plan = mkplan(("name", [{"op": "regex_replace", "pattern": "$", "replacement": "-123456789"}]))
    rep = clean(conn(url), plan, None, run_dir=tmp_path / "run")
    assert rep.cells_changed == 0 and rep.cells_flagged == 2
    assert (tmp_path / "run" / "flags.csv").read_text().count("Result exceeds column length (8)") == 2
    assert [r[1] for r in rows(url, "people_cleaned")] == ["ab", "x"]


def test_not_null_with_null_if_rejected(tmp_path):
    plan = mkplan(("note", [{"op": "null_if", "values": ["n1"]}]))
    with pytest.raises(PlanError, match="NOT NULL|nullable|null"):
        clean(conn(make_db(tmp_path)), plan, None, run_dir=tmp_path / "r")


def test_schema_attached_database(tmp_path):
    # SQLite has no schemas to speak of; only check that schema=None path reflects fine
    url = make_db(tmp_path)
    assert conn(url).column_info()
