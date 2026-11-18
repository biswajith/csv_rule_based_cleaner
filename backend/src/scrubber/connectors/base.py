"""Connector / Writer interfaces and source specs (spec §7)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Annotated, Any, Iterator, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator

RowKey = Union[str, int]
Record = dict[str, Any]
Batch = list[tuple[RowKey, Record]]
WriteBatch = list[tuple[RowKey, Record, set[str]]]

EXTRA_KEY = "__extra__"  # reserved record key: surplus fields of a long CSV row


class _Opaque:
    """Marker value (never str, never None): a cell whose dotted path cannot be addressed in
    this document (Mongo: a parent is a scalar/null/array, or the path is itself a document).
    String-only ops leave it alone, so no change is ever written to it (spec §7.3)."""

    _instance: "_Opaque | None" = None

    def __new__(cls) -> "_Opaque":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:
        return "<not addressable>"

    def __copy__(self) -> "_Opaque":
        return self

    def __deepcopy__(self, memo: Any) -> "_Opaque":
        return self


OPAQUE = _Opaque()


class SourceError(RuntimeError):
    pass


class OutputExistsError(SourceError):
    """The output target exists and `overwrite` was not set (API: 409)."""


class CSVSource(BaseModel):
    kind: Literal["csv"] = "csv"
    path: str
    delimiter: str = ","
    encoding: str = "utf-8-sig"
    display_name: str | None = None  # API uploads: sanitised original name (description, output stem)


class SQLSource(BaseModel):
    """Either a full `url`, or the connection fields (`dialect`, `host`, `database`, optional
    `port`/`username`/`password`) from which the URL is built — with user name and password
    escaped, so characters like `@`, `/` or `:` in a password are safe. After validation
    `url` is always set and the separate fields are cleared (one copy of the secret)."""

    model_config = ConfigDict(populate_by_name=True)

    kind: Literal["sql"] = "sql"
    url: str | None = None
    dialect: Literal["postgresql", "mysql"] | None = None
    host: str | None = None
    port: int | None = Field(default=None, ge=1, le=65535)
    database: str | None = None
    username: str | None = None
    password: str | None = None
    table: str
    db_schema: str | None = Field(default=None, alias="schema")
    key_column: str | None = None

    @model_validator(mode="after")
    def _build_url(self) -> SQLSource:
        parts = (self.dialect, self.host, self.port, self.database, self.username, self.password)
        if self.url:
            if any(p not in (None, "") for p in parts):
                raise ValueError("give either url or the connection fields, not both")
            return self
        missing = [n for n, v in (("dialect", self.dialect), ("host", self.host),
                                  ("database", self.database)) if not v]
        if missing:
            raise ValueError(f"url or connection fields required (missing: {', '.join(missing)})")
        import sqlalchemy as sa

        self.url = sa.engine.URL.create(
            self.dialect, username=self.username or None, password=self.password or None,
            host=self.host.strip(), port=self.port, database=self.database.strip(),
        ).render_as_string(hide_password=False)
        self.dialect = self.host = self.port = self.database = None
        self.username = self.password = None
        return self


class MongoSource(BaseModel):
    kind: Literal["mongo"] = "mongo"
    uri: str
    database: str
    collection: str
    filter: dict[str, Any] = Field(default_factory=dict)


SourceSpec = Annotated[Union[CSVSource, SQLSource, MongoSource], Field(discriminator="kind")]


class ColumnInfo(BaseModel):
    name: str
    cleanable: bool
    nullable: bool = True
    max_length: int | None = None


class Writer(ABC):
    """Receives cleaned batches; the set holds the columns that changed per row."""

    target: str = ""

    @abstractmethod
    def write(self, batch: WriteBatch) -> None: ...

    def close(self) -> None:  # noqa: B027
        pass

    def __enter__(self) -> "Writer":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


class Connector(ABC):
    description: str
    null_equals_empty: bool = False

    @abstractmethod
    def column_info(self) -> list[ColumnInfo]: ...

    @abstractmethod
    def count(self) -> int | None: ...

    @abstractmethod
    def sample(self, n: int) -> list[Record]: ...

    @abstractmethod
    def iter_batches(self, size: int) -> Iterator[Batch]: ...

    @abstractmethod
    def writer(self, *, run_dir: Path, in_place: bool, overwrite: bool) -> Writer: ...

    def check_target(self, *, in_place: bool, overwrite: bool) -> None:  # noqa: B027
        """Raise SourceError / OutputExistsError if `writer(...)` would fail on its preconditions,
        without creating anything. Lets the API answer 400/409 before queueing a run."""

    def close(self) -> None:  # noqa: B027
        pass

    def __enter__(self) -> "Connector":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
