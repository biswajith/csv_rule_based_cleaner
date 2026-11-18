"""MongoDB connector (spec §7.3). Dotted-path view of documents; writers touch only changed paths."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Iterator

from .base import (
    OPAQUE,
    Batch,
    ColumnInfo,
    Connector,
    MongoSource,
    OutputExistsError,
    Record,
    SourceError,
    WriteBatch,
    Writer,
)

_MISSING = object()


def flatten(doc: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    """Nested documents -> dotted paths (recursive). Arrays and empty documents are leaves."""
    out: dict[str, Any] = {}
    for k, v in doc.items():
        path = f"{prefix}{k}"
        if isinstance(v, dict) and v:
            out.update(flatten(v, path + "."))
        else:
            out[path] = v
    return out


def get_path(doc: dict[str, Any], path: str) -> Any:
    cur: Any = doc
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return _MISSING
        cur = cur[part]
    return cur


def set_path(doc: dict[str, Any], path: str, value: Any) -> bool:
    """Set `a.b.c` on `doc`, creating missing (or null) intermediate documents.
    Returns False (doc untouched) when an intermediate holds a non-document value."""
    parts = path.split(".")
    cur = doc
    for part in parts[:-1]:
        nxt = cur.get(part)
        if nxt is None:
            cur[part] = nxt = {}
        elif not isinstance(nxt, dict):
            return False
        cur = nxt
    cur[parts[-1]] = value
    return True


class MongoConnector(Connector):
    def __init__(self, spec: MongoSource, client: Any = None,
                 columns: list[ColumnInfo] | None = None):
        """`columns`: the column list discovered by an earlier open of the same source. Passing it
        keeps the column set stable between planning and running (discovery is a random sample)."""
        self.spec = spec
        self.description = f"MongoDB {spec.database}.{spec.collection}"
        self._own_client = client is None
        try:
            if client is None:
                import pymongo

                client = pymongo.MongoClient(spec.uri, serverSelectionTimeoutMS=5000)
            self._client = client
            self._db = client[spec.database]
            self._coll = self._db[spec.collection]
            from .. import config

            self._columns = (list(columns) if columns is not None
                             else self._discover(config.get_settings().sample_size))
        except SourceError:
            self.close()
            raise
        except Exception as e:  # PyMongoError, bad URI: the URI itself is never echoed
            self.close()
            raise SourceError(f"cannot read {self.description}: {type(e).__name__}") from e

    # ------------------------------------------------------------------ reading

    def _sample_docs(self, n: int) -> list[dict[str, Any]]:
        pipeline = [{"$match": self.spec.filter}, {"$sample": {"size": n}}]
        return list(self._coll.aggregate(pipeline))

    def _discover(self, n: int) -> list[ColumnInfo]:
        has_str: dict[str, bool] = {}
        only_null: dict[str, bool] = {}
        for d in self._sample_docs(n):
            for path, v in flatten({k: x for k, x in d.items() if k != "_id"}).items():
                has_str[path] = has_str.get(path, False) or isinstance(v, str)
                only_null[path] = only_null.get(path, True) and v is None
        # cleanable: at least one str value in the sample, or only null/absent values
        return [ColumnInfo(name=p, cleanable=has_str[p] or only_null[p]) for p in sorted(has_str)]

    def _record(self, doc: dict[str, Any]) -> Record:
        flat = flatten({k: v for k, v in doc.items() if k != "_id"})
        containers = {".".join(p.split(".")[:i]) for p in flat for i in range(1, p.count(".") + 1)}
        rec: Record = {}
        for c in self._columns:
            name = c.name
            if name in flat:
                rec[name] = flat[name]
            elif name in containers or self._blocked(flat, name):
                rec[name] = OPAQUE  # this document cannot hold a value at that path
            else:
                rec[name] = None
        return rec

    @staticmethod
    def _blocked(flat: dict[str, Any], name: str) -> bool:
        """A proper prefix of `name` holds a scalar/null/array in this document."""
        parts = name.split(".")
        for i in range(1, len(parts)):
            v = flat.get(".".join(parts[:i]), _MISSING)
            if v is not _MISSING and not isinstance(v, dict):
                return True
        return False

    def column_info(self) -> list[ColumnInfo]:
        return list(self._columns)

    def count(self) -> int | None:
        return self._coll.count_documents(self.spec.filter)

    def sample(self, n: int) -> list[Record]:
        return [self._record(d) for d in self._sample_docs(n)]

    def iter_batches(self, size: int) -> Iterator[Batch]:
        last: Any = _MISSING
        while True:
            q: dict[str, Any] = dict(self.spec.filter)
            if last is not _MISSING:
                # $expr compares with BSON cross-type ordering (same as sort), so mixed _id
                # types are visited exactly once; a plain {"_id": {"$gt": x}} is type-bracketed
                # and would skip every document whose _id type sorts after `last`'s.
                after = {"$expr": {"$gt": ["$_id", {"$literal": last}]}}
                q = {"$and": [q, after]} if q else after
            docs = list(self._coll.find(q).sort("_id", 1).limit(size))
            if not docs:
                return
            last = docs[-1]["_id"]
            yield [(d["_id"], self._record(d)) for d in docs]
            if len(docs) < size:
                return

    # ------------------------------------------------------------------ writing

    @property
    def _target_name(self) -> str:
        return f"{self.spec.collection}_cleaned"

    def check_target(self, *, in_place: bool, overwrite: bool) -> None:
        if not in_place and not overwrite and self._target_name in self._db.list_collection_names():
            raise OutputExistsError(f"collection {self.spec.database}.{self._target_name} "
                                    "already exists")

    def writer(self, *, run_dir: Path, in_place: bool, overwrite: bool) -> Writer:
        if in_place:
            return _InPlaceWriter(self)
        return _NewCollectionWriter(self, overwrite)

    def close(self) -> None:
        if getattr(self, "_own_client", False) and getattr(self, "_client", None) is not None:
            self._client.close()


class _NewCollectionWriter(Writer):
    def __init__(self, conn: MongoConnector, overwrite: bool):
        self._src = conn._coll
        name = conn._target_name
        db = conn._db
        if name in db.list_collection_names():
            if not overwrite:
                raise OutputExistsError(f"collection {conn.spec.database}.{name} already exists")
            db.drop_collection(name)
        self._dst = db[name]
        self.target = f"collection {conn.spec.database}.{name}"

    def write(self, batch: WriteBatch) -> None:
        if not batch:
            return
        originals = {d["_id"]: d for d in self._src.find({"_id": {"$in": [k for k, _, _ in batch]}})}
        docs = []
        for key, rec, changed in batch:
            orig = originals.get(key)
            if orig is None:
                raise SourceError(f"document {key!r} disappeared from the source during the run")
            doc = copy.deepcopy(orig)
            for path in changed:
                if rec.get(path) is not OPAQUE:
                    set_path(doc, path, rec.get(path))
            docs.append(doc)
        self._dst.insert_many(docs)


class _InPlaceWriter(Writer):
    def __init__(self, conn: MongoConnector):
        self._coll = conn._coll
        self.target = f"collection {conn.spec.database}.{conn.spec.collection} (in place)"

    def write(self, batch: WriteBatch) -> None:
        from pymongo import UpdateOne

        ops = [UpdateOne({"_id": key}, {"$set": {p: rec.get(p) for p in changed
                                                 if rec.get(p) is not OPAQUE}})
               for key, rec, changed in batch if changed]
        if ops:
            self._coll.bulk_write(ops, ordered=True)
