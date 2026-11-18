"""SQL connector (spec §7.2): SQLAlchemy 2 Core, keyset pagination, per-batch transactions."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterator

import sqlalchemy as sa
from sqlalchemy.exc import ArgumentError, NoSuchTableError, SQLAlchemyError

from .base import (
    Batch,
    ColumnInfo,
    Connector,
    OutputExistsError,
    Record,
    SourceError,
    SQLSource,
    WriteBatch,
    Writer,
)


# Bare dialect names map to the drivers this package installs (spec §7.2). An explicit
# `dialect+driver://` URL is used as given.
_DEFAULT_DRIVERS = {
    "postgres": "postgresql+psycopg",  # Heroku-style scheme SQLAlchemy 2 rejects
    "postgresql": "postgresql+psycopg",
    "mysql": "mysql+pymysql",
    "mariadb": "mariadb+pymysql",
}


def normalize_url(url: sa.engine.URL) -> sa.engine.URL:
    """Pick the bundled driver for bare `postgresql://` / `mysql://` URLs and default MySQL /
    MariaDB connections to utf8mb4 (the server's default charset may be latin1 or 3-byte utf8,
    which corrupts or rejects non-ASCII text)."""
    driver = _DEFAULT_DRIVERS.get(url.drivername)
    if driver:
        url = url.set(drivername=driver)
    if url.get_backend_name() in ("mysql", "mariadb") and "charset" not in url.query:
        url = url.update_query_dict({"charset": "utf8mb4"})
    return url


def _cleanable(t: sa.types.TypeEngine) -> bool:
    """Free-text string columns only: VARCHAR/CHAR/TEXT and dialect variants. Enumerated types
    (ENUM, MySQL SET) only accept fixed values, so they are read-only context."""
    from sqlalchemy.dialects.mysql import SET

    return isinstance(t, sa.String) and not isinstance(t, (sa.Enum, SET))


def describe_url(url: sa.engine.URL, table: str) -> str:
    """`SQL <driver>://<host>[:port]/<database> / <table>`: never user, password or query."""
    port = f":{url.port}" if url.port else ""
    return f"SQL {url.drivername}://{url.host or ''}{port}/{url.database or ''} / {table}"


class SQLConnector(Connector):
    def __init__(self, spec: SQLSource):
        self.spec = spec
        self._secret = ""
        try:
            url = normalize_url(sa.engine.make_url(spec.url))
            self._secret = url.password or ""
            self.description = describe_url(url, spec.table)
            self.engine = sa.create_engine(url, pool_pre_ping=True)
        except ArgumentError as e:
            # the message of ArgumentError may quote the URL: never forward it
            raise SourceError("invalid database URL") from e
        except ModuleNotFoundError as e:
            raise SourceError(f"no database driver for {url.drivername!r} (missing Python "
                              f"package {e.name!r}); install it, or use postgresql://, "
                              "mysql:// or sqlite:// URLs") from e
        except Exception as e:
            raise SourceError(self._clean(f"cannot create database engine: {e}")) from e
        try:
            self.table = sa.Table(spec.table, sa.MetaData(schema=spec.db_schema),
                                  autoload_with=self.engine)
        except NoSuchTableError as e:
            self.engine.dispose()
            raise SourceError(f"table {spec.table!r} not found") from e
        except Exception as e:
            self.engine.dispose()
            raise SourceError(self._clean(f"cannot open table {spec.table!r}: {e}")) from e
        try:
            self.key_column = self._resolve_key()
        except Exception:
            self.engine.dispose()
            raise

    def _clean(self, msg: str) -> str:
        return msg.replace(self._secret, "***") if self._secret else msg

    # ------------------------------------------------------------------ key

    def _resolve_key(self) -> str:
        spec = self.spec
        if spec.key_column:
            if spec.key_column not in self.table.c:
                raise SourceError(f"key column {spec.key_column!r} not in table "
                                  f"(columns: {', '.join(c.name for c in self.table.columns)})")
            key = self.table.c[spec.key_column]
            try:
                with self.engine.connect() as conn:
                    total, non_null, distinct = conn.execute(sa.select(
                        sa.func.count(), sa.func.count(key), sa.func.count(sa.distinct(key))
                    ).select_from(self.table)).one()
            except SQLAlchemyError as e:
                raise SourceError(self._clean(f"cannot check key column: {e}")) from e
            if total != non_null or total != distinct:
                raise SourceError(f"key column {spec.key_column!r} must be unique and non-null")
            return spec.key_column
        pk = [c.name for c in self.table.primary_key.columns]
        if len(pk) != 1:
            raise SourceError("table has no single-column primary key; pass key_column naming "
                              "a unique, non-null column")
        return pk[0]

    # ------------------------------------------------------------------ reading

    def column_info(self) -> list[ColumnInfo]:
        out = []
        for c in self.table.columns:
            out.append(ColumnInfo(
                name=c.name,
                cleanable=_cleanable(c.type),
                nullable=bool(c.nullable),
                max_length=getattr(c.type, "length", None) if isinstance(c.type, sa.String) else None,
            ))
        return out

    def count(self) -> int | None:
        with self.engine.connect() as conn:
            return conn.execute(sa.select(sa.func.count()).select_from(self.table)).scalar_one()

    def sample(self, n: int) -> list[Record]:
        key = self.table.c[self.key_column]
        with self.engine.connect() as conn:
            return [dict(r._mapping) for r in
                    conn.execute(sa.select(self.table).order_by(key).limit(n))]

    def iter_batches(self, size: int) -> Iterator[Batch]:
        # Keyset pagination, one short-lived connection per batch: no cursor stays open across
        # a yield (the writer commits in between; SQLite would report "database is locked").
        key = self.table.c[self.key_column]
        last: Any = None
        while True:
            q = sa.select(self.table).order_by(key).limit(size)
            if last is not None:
                q = q.where(key > last)
            with self.engine.connect() as conn:
                rows = [dict(r._mapping) for r in conn.execute(q)]
            if not rows:
                return
            last = rows[-1][self.key_column]
            yield [(r[self.key_column], r) for r in rows]
            if len(rows) < size:
                return

    # ------------------------------------------------------------------ writing

    @property
    def _target_name(self) -> str:
        return f"{self.spec.table}_cleaned"

    def _qualified(self, name: str) -> str:
        return f"{self.spec.db_schema}.{name}" if self.spec.db_schema else name

    def _target_exists(self) -> bool:
        return sa.inspect(self.engine).has_table(self._target_name, schema=self.spec.db_schema)

    def check_target(self, *, in_place: bool, overwrite: bool) -> None:
        if not in_place and not overwrite and self._target_exists():
            raise OutputExistsError(f"table {self._qualified(self._target_name)} already exists")

    def writer(self, *, run_dir: Path, in_place: bool, overwrite: bool) -> Writer:
        if in_place:
            return _InPlaceWriter(self)
        return _NewTableWriter(self, overwrite)

    def close(self) -> None:
        self.engine.dispose()


class _NewTableWriter(Writer):
    def __init__(self, conn: SQLConnector, overwrite: bool):
        self._engine = conn.engine
        src = conn.table
        schema = conn.spec.db_schema
        name = conn._target_name
        try:
            if conn._target_exists():
                if not overwrite:
                    raise OutputExistsError(f"table {conn._qualified(name)} already exists")
                old = sa.Table(name, sa.MetaData(schema=schema), autoload_with=self._engine)
                old.drop(self._engine)
            md = sa.MetaData(schema=schema)
            # same names/types/PK only; indexes, FKs, defaults are deliberately not copied
            self._table = sa.Table(name, md, *[
                sa.Column(c.name, c.type, primary_key=c.primary_key, nullable=c.nullable,
                          autoincrement=False)
                for c in src.columns])
            md.create_all(self._engine)
        except SQLAlchemyError as e:
            raise SourceError(conn._clean(f"cannot create table {conn._qualified(name)}: {e}")) from e
        self.target = f"table {conn._qualified(name)}"
        self._cols = [c.name for c in src.columns]

    def write(self, batch: WriteBatch) -> None:
        rows = [{c: rec.get(c) for c in self._cols} for _, rec, _ in batch]
        if not rows:
            return
        with self._engine.begin() as cx:
            cx.execute(self._table.insert(), rows)


class _InPlaceWriter(Writer):
    def __init__(self, conn: SQLConnector):
        self._engine = conn.engine
        self._table = conn.table
        self._key = conn.key_column
        schema = conn.spec.db_schema
        self.target = f"table {conn._qualified(conn.spec.table)} (in place)"

    def write(self, batch: WriteBatch) -> None:
        groups: dict[tuple[str, ...], list[dict[str, Any]]] = {}
        for key, rec, changed in batch:
            if not changed:
                continue
            cols = tuple(sorted(changed))
            row = {"b_key": key}
            row.update({f"v_{c}": rec.get(c) for c in cols})
            groups.setdefault(cols, []).append(row)
        if not groups:
            return
        t = self._table
        with self._engine.begin() as cx:  # one transaction per batch
            for cols, rows in groups.items():
                stmt = (sa.update(t).where(t.c[self._key] == sa.bindparam("b_key"))
                        .values({c: sa.bindparam(f"v_{c}") for c in cols}))
                cx.execute(stmt, rows)
