from __future__ import annotations

from typing import Any

from .base import (
    ColumnInfo,
    Connector,
    CSVSource,
    MongoSource,
    OutputExistsError,
    SourceError,
    SourceSpec,
    SQLSource,
    Writer,
)


def open_source(spec: CSVSource | SQLSource | MongoSource, mongo_client: Any = None,
                columns: list[ColumnInfo] | None = None) -> Connector:
    """Open a connector; heavy backends are imported lazily. `columns` (Mongo only) reuses a
    column list discovered earlier instead of re-sampling."""
    if isinstance(spec, CSVSource):
        from .csv_source import CSVConnector

        return CSVConnector(spec)
    if isinstance(spec, SQLSource):
        from .sql_source import SQLConnector

        return SQLConnector(spec)
    if isinstance(spec, MongoSource):
        from .mongo_source import MongoConnector

        return MongoConnector(spec, client=mongo_client, columns=columns)
    raise SourceError(f"unknown source {spec!r}")


__all__ = [
    "ColumnInfo", "Connector", "CSVSource", "MongoSource", "OutputExistsError", "SourceError",
    "SourceSpec", "SQLSource", "Writer", "open_source",
]
