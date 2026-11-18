"""MySQL and PostgreSQL (spec §7.2).

URL handling is tested always. The server tests run against real databases when
SCRUBBER_TEST_MYSQL_URL / SCRUBBER_TEST_POSTGRES_URL point at an empty scratch database
(the tests create and drop their own tables), e.g.

    SCRUBBER_TEST_MYSQL_URL=mysql://root@127.0.0.1:3306/scrub_test
    SCRUBBER_TEST_POSTGRES_URL=postgresql://postgres@127.0.0.1:5432/scrub_test
"""

from __future__ import annotations

import os
import uuid

import pytest
import sqlalchemy as sa
from util import mkplan

from scrubber.cleaner import clean
from scrubber.connectors import OutputExistsError, SourceError, SQLSource, open_source
from scrubber.connectors.sql_source import normalize_url

TRIM = {"op": "trim"}

# ------------------------------------------------------------------ URL handling (no server)


@pytest.mark.parametrize("given, driver", [
    ("postgres://u:p@h/db", "postgresql+psycopg"),
    ("postgresql://u:p@h/db", "postgresql+psycopg"),
    ("postgresql+psycopg2://u:p@h/db", "postgresql+psycopg2"),  # explicit driver kept
    ("mysql://u:p@h/db", "mysql+pymysql"),
    ("mariadb://u:p@h/db", "mariadb+pymysql"),
    ("mysql+mysqldb://u:p@h/db", "mysql+mysqldb"),
    ("sqlite:///x.db", "sqlite"),
])
def test_normalize_url_driver(given, driver):
    assert normalize_url(sa.engine.make_url(given)).drivername == driver


def test_normalize_url_mysql_charset():
    assert normalize_url(sa.engine.make_url("mysql://h/db")).query == {"charset": "utf8mb4"}
    kept = normalize_url(sa.engine.make_url("mysql://h/db?charset=latin1"))
    assert kept.query == {"charset": "latin1"}
    assert normalize_url(sa.engine.make_url("postgresql://h/db")).query == {}


def test_missing_driver_message():
    with pytest.raises(SourceError, match="no database driver .*mysqldb|MySQLdb"):
        open_source(SQLSource(url="mysql+mysqldb://u:secret@127.0.0.1:1/db", table="t"))


# ------------------------------------------------------------------ real servers

SERVERS = [
    pytest.param("SCRUBBER_TEST_MYSQL_URL", id="mysql"),
    pytest.param("SCRUBBER_TEST_POSTGRES_URL", id="postgres"),
]


@pytest.fixture(params=SERVERS)
def db(request):
    url = os.environ.get(request.param)
    if not url:
        pytest.skip(f"{request.param} not set")
    engine = sa.create_engine(normalize_url(sa.engine.make_url(url)))
    table = f"people_{uuid.uuid4().hex[:8]}"
    yield url, engine, table
    with engine.begin() as cx:
        for t in (f"{table}_cleaned", table):
            cx.exec_driver_sql(f"DROP TABLE IF EXISTS {t}")
    engine.dispose()


def make_table(engine, table, *, key="id INTEGER PRIMARY KEY", rows=None):
    is_mysql = engine.dialect.name == "mysql"
    status = "ENUM('new','old')" if is_mysql else "VARCHAR(3) CHECK (status IN ('new','old'))"
    extra = ", tags SET('a','b')" if is_mysql else ", tags TEXT[]"
    with engine.begin() as cx:
        cx.exec_driver_sql(
            f"CREATE TABLE {table} ({key}, name VARCHAR(12), email TEXT, age INTEGER, "
            f"status {status}{extra})")
        for r in rows if rows is not None else [
                (1, "  José  ", " A@X.COM ", 30), (2, "Zoë 😀 ", "b@x.com", 40),
                (3, " ann ", None, 50), (4, "bob", " d@x.com", 60)]:
            cx.execute(sa.text(f"INSERT INTO {table} (id, name, email, age, status) "
                               "VALUES (:i, :n, :e, :a, 'new')"),
                       {"i": r[0], "n": r[1], "e": r[2], "a": r[3]})


def read(engine, table):
    with engine.connect() as cx:
        return [tuple(r) for r in cx.exec_driver_sql(
            f"SELECT id, name, email, age FROM {table} ORDER BY id")]


def test_column_info(db):
    url, engine, table = db
    make_table(engine, table)
    c = open_source(SQLSource(url=url, table=table))
    info = {i.name: i for i in c.column_info()}
    assert c.key_column == "id"
    assert info["name"].cleanable and info["name"].max_length == 12
    assert info["email"].cleanable
    assert not info["age"].cleanable
    assert not info["tags"].cleanable  # MySQL SET / Postgres array
    if engine.dialect.name == "mysql":
        assert not info["status"].cleanable  # ENUM
    assert "secret" not in c.description and c.description.startswith("SQL ")
    c.close()


def test_new_table_keeps_unicode_and_types(db, tmp_path):
    url, engine, table = db
    make_table(engine, table)
    plan = mkplan(("name", [TRIM]), ("email", [TRIM, {"op": "case", "mode": "lower"}]))
    rep = clean(open_source(SQLSource(url=url, table=table)), plan, None, run_dir=tmp_path / "r")
    assert rep.status == "done" and rep.rows == 4, rep.error
    assert read(engine, f"{table}_cleaned") == [
        (1, "José", "a@x.com", 30), (2, "Zoë 😀", "b@x.com", 40),
        (3, "ann", None, 50), (4, "bob", "d@x.com", 60)]
    assert read(engine, table)[0][1] == "  José  "  # source untouched
    insp = sa.inspect(engine)
    assert insp.get_pk_constraint(f"{table}_cleaned")["constrained_columns"] == ["id"]
    cols = {c["name"]: c["type"] for c in insp.get_columns(f"{table}_cleaned")}
    assert getattr(cols["name"], "length", None) == 12


def test_existing_target_then_overwrite(db, tmp_path):
    url, engine, table = db
    make_table(engine, table)
    plan = mkplan(("name", [TRIM]))
    clean(open_source(SQLSource(url=url, table=table)), plan, None, run_dir=tmp_path / "r1")
    with pytest.raises(OutputExistsError):
        clean(open_source(SQLSource(url=url, table=table)), plan, None, run_dir=tmp_path / "r2")
    rep = clean(open_source(SQLSource(url=url, table=table)), plan, None, overwrite=True,
                run_dir=tmp_path / "r3")
    assert rep.status == "done" and len(read(engine, f"{table}_cleaned")) == 4


def test_in_place_and_rerun_is_noop(db, tmp_path):
    url, engine, table = db
    make_table(engine, table)
    c = open_source(SQLSource(url=url, table=table))
    rep = clean(c, mkplan(("name", [TRIM]), ("email", [TRIM])), None, in_place=True,
                run_dir=tmp_path / "r")
    assert rep.status == "done" and rep.cells_changed == 5, rep.error
    assert read(engine, table) == [(1, "José", "A@X.COM", 30), (2, "Zoë 😀", "b@x.com", 40),
                                   (3, "ann", None, 50), (4, "bob", "d@x.com", 60)]
    rep2 = clean(c, mkplan(("name", [TRIM])), None, in_place=True, run_dir=tmp_path / "r2")
    assert rep2.cells_changed == 0
    c.close()


def test_string_key_column_pagination(db, tmp_path, monkeypatch):
    url, engine, table = db
    with engine.begin() as cx:
        cx.exec_driver_sql(f"CREATE TABLE {table} (code VARCHAR(10) NOT NULL UNIQUE, "
                           "name VARCHAR(20))")
        cx.execute(sa.text(f"INSERT INTO {table} VALUES (:c, :n)"),
                   [{"c": f"k{i:03d}", "n": f" n{i} "} for i in range(25)])
    monkeypatch.setenv("BATCH_SIZE", "7")
    from scrubber.config import get_settings
    get_settings.cache_clear()
    try:
        c = open_source(SQLSource(url=url, table=table, key_column="code"))
        assert sum(len(b) for b in c.iter_batches(7)) == 25
        rep = clean(c, mkplan(("name", [TRIM])), None, in_place=True, run_dir=tmp_path / "r")
        assert rep.status == "done" and rep.cells_changed == 25, rep.error
        c.close()
    finally:
        get_settings.cache_clear()
    with engine.connect() as cx:
        assert {r[0] for r in cx.exec_driver_sql(f"SELECT name FROM {table}")} == {
            f"n{i}" for i in range(25)}


def test_too_long_value_is_flagged_not_written(db, tmp_path):
    url, engine, table = db
    make_table(engine, table, rows=[(1, "abcdefghij", "e", 1)])
    plan = mkplan(("name", [{"op": "regex_replace", "pattern": "$", "replacement": "-123"}]))
    rep = clean(open_source(SQLSource(url=url, table=table)), plan, None, in_place=True,
                run_dir=tmp_path / "r")
    assert rep.status == "done" and rep.cells_changed == 0 and rep.cells_flagged == 1
    assert read(engine, table)[0][1] == "abcdefghij"


# ------------------------------------------------------------------ connection fields

TRICKY = "p@ss/w:rd?#%"


def test_fields_build_escaped_url_and_clear_secret_copies():
    s = SQLSource(dialect="postgresql", host=" db.example ", port=6543, database="crm",
                  username="me", password=TRICKY, table="t")
    u = sa.engine.make_url(s.url)
    assert (u.drivername, u.host, u.port, u.database, u.username, u.password) == (
        "postgresql", "db.example", 6543, "crm", "me", TRICKY)
    assert s.password is None and s.host is None and s.dialect is None


def test_fields_optional_parts_and_validation():
    s = SQLSource(dialect="mysql", host="h", database="d", username="", password="", table="t")
    assert s.url == "mysql://h/d"
    with pytest.raises(ValueError, match="missing: host, database"):
        SQLSource(dialect="mysql", table="t")
    with pytest.raises(ValueError, match="either url or"):
        SQLSource(url="mysql://h/d", host="h", table="t")
    with pytest.raises(ValueError):
        SQLSource(dialect="oracle", host="h", database="d", table="t")


def test_api_accepts_connection_fields(db, tmp_path):
    """A user whose password needs escaping connects through the API's field form."""
    from fastapi.testclient import TestClient

    from scrubber.api import create_app

    url, engine, table = db
    make_table(engine, table)
    u = sa.engine.make_url(url)
    user = f"scrub_{uuid.uuid4().hex[:6]}"
    mysql = engine.dialect.name == "mysql"
    with engine.begin() as cx:
        if mysql:
            cx.exec_driver_sql(f"CREATE USER '{user}'@'%%' IDENTIFIED BY '{TRICKY.replace('%', '%%')}'")
            cx.exec_driver_sql(f"GRANT ALL ON {u.database}.* TO '{user}'@'%%'")
        else:
            cx.exec_driver_sql(f"CREATE ROLE {user} LOGIN PASSWORD '{TRICKY.replace('%', '%%')}'")
            cx.exec_driver_sql(f"GRANT SELECT ON {table} TO {user}")
    try:
        client = TestClient(create_app(port=8000), base_url="http://127.0.0.1:8000")
        r = client.post("/api/sources", json={
            "kind": "sql", "dialect": "mysql" if mysql else "postgresql", "host": u.host,
            "port": u.port, "database": u.database, "username": user, "password": TRICKY,
            "table": table})
        assert r.status_code == 200, r.text
        assert TRICKY not in r.text and r.json()["count"] == 4
    finally:
        with engine.begin() as cx:
            if mysql:
                cx.exec_driver_sql(f"DROP USER '{user}'@'%%'")
            else:
                cx.exec_driver_sql(f"REVOKE ALL ON {table} FROM {user}")
                cx.exec_driver_sql(f"DROP ROLE {user}")
