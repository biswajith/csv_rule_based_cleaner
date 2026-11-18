from __future__ import annotations

import mongomock
import pytest
from bson import ObjectId
from util import mkplan

from scrubber.cleaner import clean
from scrubber.connectors import MongoSource, OutputExistsError, open_source
from scrubber.connectors.mongo_source import flatten, set_path

TRIM = {"op": "trim"}


@pytest.fixture(autouse=True)
def _mongomock_sort_shim(monkeypatch):
    """mongomock 4.3 predates pymongo>=4.14's `sort` argument on UpdateOne (test-env shim)."""
    from mongomock.collection import BulkOperationBuilder

    orig = BulkOperationBuilder.add_update

    def add_update(self, *a, **kw):
        kw.pop("sort", None)
        return orig(self, *a, **kw)

    monkeypatch.setattr(BulkOperationBuilder, "add_update", add_update)


@pytest.fixture
def client():
    c = mongomock.MongoClient()
    c.db.people.insert_many([
        {"_id": 1, "name": " ann ", "address": {"city": " Rome ", "geo": {"lat": 1.5}, "e": {}},
         "tags": [" a ", "b"], "age": 30, "extra": "keep"},
        {"_id": 2, "name": "bob", "address": {"city": "Oslo"}},
        {"_id": 3, "name": None, "age": "x"},
        {"_id": 4, "name": " dan "},
    ])
    return c


def conn(client, **kw):
    return open_source(MongoSource(uri="mongodb://secret:pw@h", database="db",
                                   collection="people", **kw), mongo_client=client)


def test_flatten_and_set_path():
    d = {"a": {"b": "x", "c": {}}, "l": [1], "n": None}
    assert flatten(d) == {"a.b": "x", "a.c": {}, "l": [1], "n": None}
    doc: dict = {}
    assert set_path(doc, "a.b.c", 1) and doc == {"a": {"b": {"c": 1}}}
    doc = {"a": "str"}
    assert not set_path(doc, "a.b", 1) and doc == {"a": "str"}


def test_columns_and_description(client):
    c = conn(client)
    assert "secret" not in c.description and c.description == "MongoDB db.people"
    info = {i.name: i for i in c.column_info()}
    assert set(info) == {"name", "address.city", "address.geo.lat", "address.e", "tags", "age",
                         "extra"}
    assert "_id" not in info
    assert info["name"].cleanable and info["address.city"].cleanable and info["extra"].cleanable
    assert not info["tags"].cleanable and not info["address.e"].cleanable
    assert not info["address.geo.lat"].cleanable
    assert info["age"].cleanable  # mixed int / str
    assert c.count() == 4


def test_sample_and_batches(client):
    c = conn(client)
    assert len(c.sample(10)) == 4 and len(c.sample(2)) == 2
    batches = list(c.iter_batches(3))
    assert [[k for k, _ in b] for b in batches] == [[1, 2, 3], [4]]
    rec = dict(batches[0])[2]
    assert rec["address.city"] == "Oslo" and rec["extra"] is None and rec["tags"] is None
    assert [k for b in c.iter_batches(2) for k, _ in b] == [1, 2, 3, 4]


def test_filter(client, tmp_path):
    c = conn(client, filter={"_id": {"$gte": 2}})
    assert c.count() == 3 and [k for b in c.iter_batches(2) for k, _ in b] == [2, 3, 4]
    rep = clean(c, mkplan(("name", [TRIM])), None, run_dir=tmp_path / "run")
    assert rep.rows == 3
    assert client.db.people_cleaned.count_documents({}) == 3  # only matching docs copied


def test_new_collection_preserves_everything_else(client, tmp_path):
    plan = mkplan(("name", [TRIM]), ("address.city", [TRIM]), ("age", [TRIM]))
    rep = clean(conn(client), plan, None, run_dir=tmp_path / "run")
    assert rep.status == "done" and rep.target == "collection db.people_cleaned"
    out = {d["_id"]: d for d in client.db.people_cleaned.find()}
    assert out[1] == {"_id": 1, "name": "ann", "address": {"city": "Rome", "geo": {"lat": 1.5}, "e": {}},
                      "tags": [" a ", "b"], "age": 30, "extra": "keep"}
    assert out[3] == {"_id": 3, "name": None, "age": "x"}   # absent address stays absent
    assert out[4] == {"_id": 4, "name": "dan"}
    assert client.db.people.find_one({"_id": 1})["name"] == " ann "  # source untouched


def test_existing_collection_error_then_overwrite(client, tmp_path):
    plan = mkplan(("name", [TRIM]))
    clean(conn(client), plan, None, run_dir=tmp_path / "r1")
    with pytest.raises(OutputExistsError):
        clean(conn(client), plan, None, run_dir=tmp_path / "r2")
    with pytest.raises(OutputExistsError):
        conn(client).check_target(in_place=False, overwrite=False)
    assert clean(conn(client), plan, None, overwrite=True, run_dir=tmp_path / "r3").status == "done"


def test_in_place_sets_only_changed_paths_and_null_is_null(client, tmp_path):
    plan = mkplan(("name", [TRIM, {"op": "null_if", "values": ["bob"]}]), ("address.city", [TRIM]))
    rep = clean(conn(client), plan, None, in_place=True, run_dir=tmp_path / "run")
    assert rep.status == "done" and rep.in_place, rep.error
    docs = {d["_id"]: d for d in client.db.people.find()}
    assert docs[1]["name"] == "ann" and docs[1]["address"]["city"] == "Rome"
    assert docs[1]["tags"] == [" a ", "b"] and docs[1]["extra"] == "keep"
    assert "name" in docs[2] and docs[2]["name"] is None  # null, never $unset
    assert docs[3] == {"_id": 3, "name": None, "age": "x"}
    assert not client.db.list_collection_names().count("people_cleaned")


def test_object_id_keys_in_changes_csv(tmp_path):
    c = mongomock.MongoClient()
    oid = ObjectId()
    c.db.people.insert_one({"_id": oid, "name": " x "})
    clean(conn(c), mkplan(("name", [TRIM])), None, run_dir=tmp_path / "run")
    assert str(oid) in (tmp_path / "run" / "changes.csv").read_text()
