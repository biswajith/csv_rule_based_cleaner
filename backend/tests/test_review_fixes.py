"""Regression tests for the phase-4 review findings (docs/plans/phase-4-review-fixes.md)."""

from __future__ import annotations

import itertools
import json

import pytest
from util import approved, infos, mkplan

from scrubber.ops import _accept, apply_row, check_plan, op_parse_date
from scrubber.plan import ParseDate


# ------------------------------------------------------------------ F1 parse_date idempotence

FORMATS = ["%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d.%m.%y", "%d %b %Y", "%B %d, %Y", "%Y%m%d",
           "%d-%m-%Y", "%y-%m-%d"]


@pytest.mark.parametrize("fmt,day_first", list(itertools.product(FORMATS, [False, True])))
def test_parse_date_idempotent(fmt, day_first):
    op = ParseDate(output_format=fmt, day_first=day_first)
    for src in ["2001-02-03", "03/02/2001", "Feb 3 2001", "2001-12-11", "11/12/2001"]:
        once, _ = op_parse_date(op, src)
        twice, flag = op_parse_date(op, once)
        assert twice == once and flag is None, (fmt, day_first, src, once, twice)


def test_parse_date_output_format_value_unchanged_no_flag():
    op = ParseDate(output_format="%d/%m/%Y", day_first=False)
    # 03/02/2001 is already in the output format: must not be re-read as March 2nd
    assert op_parse_date(op, "03/02/2001") == ("03/02/2001", None)
    assert op_parse_date(op, " 03/02/2001 ") == (" 03/02/2001 ", None)


def test_parse_date_iso_input_ignores_day_first():
    op = ParseDate(output_format="%d.%m.%Y", day_first=True)
    assert op_parse_date(op, "2001-02-03") == ("03.02.2001", None)
    assert op_parse_date(op, "2001-2-3") == ("03.02.2001", None)
    assert op_parse_date(op, "2001/02/03") == ("03.02.2001", None)
    # non year-first values still honour day_first
    assert op_parse_date(op, "03/02/2001") == ("03.02.2001", None)


def test_parse_date_llm_candidate_uses_same_rules():
    ops = [ParseDate(output_format="%d.%m.%Y", day_first=True),
           mkplan(("d", [{"op": "llm_fix", "instruction": "x"}])).columns[0].ops[0]]
    assert _accept("2001-02-03", ops, 1, None) == "03.02.2001"
    assert _accept("03.02.2001", ops, 1, None) == "03.02.2001"
    assert _accept("garbage", ops, 1, None) is None


def test_check_plan_warns_when_output_format_not_readable_by_op():
    cols = infos("d")
    bad = mkplan(("d", [{"op": "parse_date", "output_format": "%d/%m/%Y %Z"}]))
    _, warnings = check_plan(bad, cols)
    assert any("read back" in w for w in warnings)
    ok = mkplan(("d", [{"op": "parse_date", "output_format": "%d/%m/%Y"}]))
    assert check_plan(ok, cols)[1] == []


# ------------------------------------------------------------------ shared helpers

import io  # noqa: E402

import mongomock  # noqa: E402
from bson import ObjectId  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from util import read_csv, write_csv  # noqa: E402

from scrubber.api import create_app  # noqa: E402
from scrubber.cleaner import FixerResponse, FixItem, _resolve, clean  # noqa: E402
from scrubber.config import get_settings  # noqa: E402
from scrubber.connectors import CSVSource, MongoSource, SourceError, SQLSource, open_source  # noqa: E402
from scrubber.llm import FakeLLM  # noqa: E402
from scrubber.ops import LLMKey, must_match_patterns  # noqa: E402
from scrubber.planner import PlanError, plan as make_plan  # noqa: E402

TRIM = {"op": "trim"}
BASE = "http://127.0.0.1:8000"


@pytest.fixture(autouse=True)
def _mongomock_sort_shim(monkeypatch):
    from mongomock.collection import BulkOperationBuilder

    orig = BulkOperationBuilder.add_update

    def add_update(self, *a, **kw):
        kw.pop("sort", None)
        return orig(self, *a, **kw)

    monkeypatch.setattr(BulkOperationBuilder, "add_update", add_update)


def set_batch(monkeypatch, n):
    monkeypatch.setenv("BATCH_SIZE", str(n))
    get_settings.cache_clear()


def csv_conn(tmp_path, rows, header=("id", "name"), name="in.csv", **kw):
    return open_source(CSVSource(path=str(write_csv(tmp_path / name, [list(header)] + rows)), **kw))


def mongo_conn(client, **kw):
    return open_source(MongoSource(uri="mongodb://h", database="db", collection="c", **kw),
                       mongo_client=client)


# ------------------------------------------------------------------ F2 Mongo pagination

def test_mongo_pagination_mixed_id_types(monkeypatch):
    c = mongomock.MongoClient()
    oid = ObjectId()
    ids = [1, 2, 3.5, "a", "b", oid, "c"]
    c.db.c.insert_many([{"_id": i, "name": " x "} for i in ids])
    seen = [k for b in mongo_conn(c).iter_batches(2) for k, _ in b]
    assert len(seen) == len(ids) and set(map(str, seen)) == set(map(str, ids))


def test_mongo_mixed_id_run_cleans_every_document(tmp_path, monkeypatch):
    set_batch(monkeypatch, 2)
    c = mongomock.MongoClient()
    c.db.c.insert_many([{"_id": i, "name": " x "} for i in [1, 2, "a", "b", "c", 7.5]])
    rep = clean(mongo_conn(c), mkplan(("name", [TRIM])), None, run_dir=tmp_path / "r")
    assert rep.status == "done" and rep.rows == 6
    assert all(d["name"] == "x" for d in c.db.c_cleaned.find()) and c.db.c_cleaned.count_documents({}) == 6


def test_mongo_pagination_with_filter(monkeypatch):
    c = mongomock.MongoClient()
    c.db.c.insert_many([{"_id": i, "name": "x", "keep": k}
                        for i, k in [(1, False), (2, True), ("a", False), ("b", True), (4, True)]])
    got = [k for b in mongo_conn(c, filter={"keep": True}).iter_batches(1) for k, _ in b]
    assert got == [2, 4, "b"]


class _Dropping:
    """Connector wrapper that silently loses the last row of the write pass."""

    def __init__(self, inner):
        self.inner = inner

    def __getattr__(self, name):
        return getattr(self.inner, name)

    def iter_batches(self, size):
        for batch in self.inner.iter_batches(size):
            yield batch[:-1]


def test_clean_fails_when_rows_read_differ_from_count(tmp_path):
    inner = csv_conn(tmp_path, [["1", " a"], ["2", " b"], ["3", " c"]])
    rep = clean(_Dropping(inner), mkplan(("name", [TRIM])), None, run_dir=tmp_path / "r")
    assert rep.status == "failed" and "2" in rep.error and "3" in rep.error
    assert "count" in rep.error.lower()


# ------------------------------------------------------------------ F3 accounting order

class _FailingWriter:
    def __init__(self, inner, fail_on_call):
        self.inner, self.n, self.fail_on_call = inner, 0, fail_on_call
        self.target = inner.target

    def write(self, batch):
        self.n += 1
        if self.n == self.fail_on_call:
            raise SourceError("disk full")
        self.inner.write(batch)

    def close(self):
        self.inner.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


class _FailingWriteConn:
    def __init__(self, inner, fail_on_call):
        self.inner, self.fail_on_call = inner, fail_on_call

    def __getattr__(self, name):
        return getattr(self.inner, name)

    def writer(self, **kw):
        return _FailingWriter(self.inner.writer(**kw), self.fail_on_call)


def test_new_target_writer_failure_changes_equal_committed(tmp_path, monkeypatch):
    set_batch(monkeypatch, 2)
    conn = _FailingWriteConn(csv_conn(tmp_path, [[str(i), f" n{i}"] for i in range(6)]), 2)
    rep = clean(conn, mkplan(("name", [TRIM])), None, run_dir=tmp_path / "r")
    assert rep.status == "failed" and "disk full" in rep.error
    assert rep.rows_written == 2 and rep.cells_changed == 2
    ch = read_csv(tmp_path / "r" / "changes.csv")[1:]
    assert [r[0] for r in ch] == ["0", "1"]      # nothing from the batch that failed to commit
    assert read_csv(tmp_path / "r" / "flags.csv")[1:] == []
    assert rep.columns[0].cells_changed == 2


def test_in_place_writer_failure_logs_undo_first_and_says_so(tmp_path, monkeypatch):
    set_batch(monkeypatch, 2)
    c = mongomock.MongoClient()
    c.db.c.insert_many([{"_id": i, "name": f" n{i}"} for i in range(6)])
    conn = _FailingWriteConn(mongo_conn(c), 2)
    rep = clean(conn, mkplan(("name", [TRIM])), None, in_place=True, run_dir=tmp_path / "r")
    assert rep.status == "failed" and "disk full" in rep.error
    assert rep.rows_written == 2 and rep.cells_changed == 2     # counters count committed rows only
    ch = read_csv(tmp_path / "r" / "changes.csv")[1:]
    # undo rows of the failing batch were logged before the commit attempt
    assert [r[0] for r in ch] == ["0", "1", "2", "3"]
    assert "changes.csv" in rep.error and "not committed" in rep.error


def test_changes_flushed_every_batch(tmp_path, monkeypatch):
    set_batch(monkeypatch, 2)
    seen = []

    class Spy(_FailingWriteConn):
        def writer(self, **kw):
            w = super().writer(**kw)
            orig = w.write

            def write(batch):
                orig(batch)
                seen.append(len(read_csv(tmp_path / "r" / "changes.csv")) - 1)

            w.write = write
            return w

    clean(Spy(csv_conn(tmp_path, [[str(i), f" n{i}"] for i in range(4)]), 99),
          mkplan(("name", [TRIM])), None, run_dir=tmp_path / "r")
    # new-target: rows of a batch are appended after its write succeeded, so at the time of
    # write k only the rows of batches < k are on disk (flushed)
    assert seen == [0, 2]


# ------------------------------------------------------------------ API helpers

CSV = b"id,name,email\n1, Ann ,A@X.com\n2,bob,b@x.com\n3, cy ,bad\n"
APLAN = {"columns": [{"column": "name", "ops": [TRIM]}]}


def api_client(llm=None, **kw):
    app = create_app(port=8000, llm_factory=(lambda planner: llm) if llm is not None else None, **kw)
    return TestClient(app, base_url=BASE)


def upload(c, data=CSV, name="a.csv", **form):
    return c.post("/api/uploads", files={"file": (name, io.BytesIO(data), "text/csv")}, data=form)


# ------------------------------------------------------------------ C1 / C2 / F15 / F13

def test_preview_returns_normalised_plan():
    with api_client() as c:
        sid = upload(c).json()["source_id"]
        r = c.post("/api/preview", json={"source_id": sid, "plan": APLAN}).json()
        assert set(r) == {"plan", "preview"}
        assert r["plan"]["version"] == 1 and r["plan"]["rules"] == [] and r["plan"]["notes"] == []
        op = r["plan"]["columns"][0]["ops"][0]
        assert op == {"enabled": True, "note": "", "op": "trim"}
        assert r["plan"]["columns"][0]["rationale"] == ""


def test_changes_and_flags_empty_for_run_without_files(monkeypatch):
    import threading

    import scrubber.runs as runs_mod

    gate = threading.Event()
    real = runs_mod.clean
    monkeypatch.setattr(runs_mod, "clean", lambda *a, **kw: (gate.wait(5), real(*a, **kw))[1])
    with api_client() as c:
        sid = upload(c).json()["source_id"]
        r1 = c.post("/api/runs", json={"source_id": sid, "plan": approved(APLAN)}).json()["run_id"]
        r2 = c.post("/api/runs", json={"source_id": sid, "plan": approved(APLAN)}).json()["run_id"]
        c.post(f"/api/runs/{r2}/cancel")
        gate.set()
        for _ in range(200):
            if c.get(f"/api/runs/{r2}").json()["status"] == "cancelled":
                break
            import time
            time.sleep(0.02)
        assert c.get(f"/api/runs/{r2}").json()["status"] == "cancelled"
        for what in ("changes", "flags"):
            r = c.get(f"/api/runs/{r2}/{what}")
            assert r.status_code == 200 and r.json() == {"total": 0, "items": []}
            assert c.get(f"/api/runs/{r2}/{what}?column=x").json() == {"total": 0, "items": []}
        assert c.get("/api/runs/20200101-000000-abcd/changes").status_code == 404
        assert c.get("/api/runs/20200101-000000-abcd/flags").status_code == 404
        del r1


def test_plan_validated_before_source_is_opened(monkeypatch):
    app = create_app(port=8000)
    opened = []
    with TestClient(app, base_url=BASE) as c:
        sid = upload(c).json()["source_id"]
        real = app.state.sources.open

        def spy(*a, **kw):
            opened.append(1)
            return real(*a, **kw)

        monkeypatch.setattr(app.state.sources, "open", spy)
        bad = {"columns": [{"column": "name", "ops": [{"op": "trim", "bogus": 1}]}]}
        for url in ("/api/preview", "/api/runs"):
            assert c.post(url, json={"source_id": sid, "plan": bad}).status_code == 422
        assert opened == []
        assert c.post("/api/preview", json={"source_id": "nope", "plan": APLAN}).status_code == 404


def test_oversized_content_length_rejected_before_form_parsing(monkeypatch):
    monkeypatch.setenv("MAX_UPLOAD_MB", "1")
    get_settings.cache_clear()
    with api_client() as c:
        # not even multipart: a 422 (missing file) would mean the body was parsed first
        r = c.post("/api/uploads", content=b"x" * (3 * 1024 * 1024),
                   headers={"content-type": "text/plain"})
        assert r.status_code == 413
        assert upload(c).status_code == 200


# ------------------------------------------------------------------ F6 lenient fixer items

def test_fix_item_coerces_and_tolerates_null_reason():
    it = FixItem.model_validate({"id": 3, "value": 12.5, "fixed": True, "reason": None, "x": 1})
    assert (it.id, it.value, it.fixed, it.reason) == ("3", "12.5", True, None)
    assert FixItem.model_validate({"id": "a"}).fixed is False


def _fix_conn(tmp_path, n=3):
    return csv_conn(tmp_path, [[str(i), f"bad{i}"] for i in range(n)], header=("id", "email"))


FIXPLAN = mkplan(("email", [{"op": "validate", "pattern": r"[a-z]+@x\.com", "message": "bad"},
                            {"op": "llm_fix", "instruction": "fix"}]))


def test_garbage_fixer_items_degrade_per_item(tmp_path):
    resp = {"results": [
        {"id": "0", "value": "ok@x.com", "fixed": True, "reason": None},      # null reason
        {"id": 1, "value": 5, "fixed": True},                                  # numeric id/value
        {"id": "2", "value": {"a": 1}, "fixed": "maybe"},                      # garbage item
        "not a dict", {"no": "id"},
    ]}
    llm = FakeLLM(lambda m, s: resp)
    rep = clean(_fix_conn(tmp_path), FIXPLAN, llm, run_dir=tmp_path / "r")
    assert rep.status == "done" and llm.usage.calls == 1      # no repair round, chunk not lost
    assert read_csv(tmp_path / "r" / "in.cleaned.csv")[1][1] == "ok@x.com"
    msgs = {r[0]: r[3] for r in read_csv(tmp_path / "r" / "flags.csv")[1:]}
    assert "malformed answer" in msgs["2"]                                      # fixed=false
    assert msgs["1"].startswith("LLM fix rejected")                            # "5" fails validate


# ------------------------------------------------------------------ F7 stable Mongo columns

def test_mongo_columns_are_reused_when_passed(monkeypatch):
    monkeypatch.setenv("SAMPLE_SIZE", "1")
    get_settings.cache_clear()
    c = mongomock.MongoClient()
    c.db.c.insert_many([{"_id": i, f"f{i}": "x"} for i in range(30)])
    first = mongo_conn(c)
    cols = first.column_info()
    for _ in range(5):
        again = open_source(MongoSource(uri="mongodb://h", database="db", collection="c"),
                            mongo_client=c, columns=cols)
        assert again.column_info() == cols
        assert [r for r in again.sample(5)][0].keys() == {x.name for x in cols}


def test_api_registry_keeps_mongo_columns_across_opens(monkeypatch):
    monkeypatch.setenv("SAMPLE_SIZE", "1")
    get_settings.cache_clear()
    m = mongomock.MongoClient()
    m.db.c.insert_many([{"_id": i, "name": " x ", f"f{i}": "y"} for i in range(30)])
    with api_client(mongo_client=m) as c:
        info = c.post("/api/sources", json={"kind": "mongo", "uri": "mongodb://h", "database": "db",
                                            "collection": "c"}).json()
        names = [x["name"] for x in info["columns"]]
        for _ in range(4):
            prof = c.get(f"/api/sources/{info['source_id']}/profile").json()
            assert [x["name"] for x in prof["columns"]] == names
        plan = {"columns": [{"column": "name", "ops": [TRIM]}]}
        assert c.post("/api/preview", json={"source_id": info["source_id"], "plan": plan}
                      ).status_code == 200


# ------------------------------------------------------------------ F8 description leak

def test_sql_description_has_no_credentials_or_query():
    import sqlalchemy as sa

    from scrubber.connectors.sql_source import describe_url

    url = sa.engine.make_url("postgresql+psycopg2://bob:hunter2@db.example.com:5432/shop"
                             "?sslmode=require&password=hunter2")
    assert describe_url(url, "people") == "SQL postgresql+psycopg2://db.example.com:5432/shop / people"
    assert describe_url(sa.engine.make_url("mysql://u@h/d"), "t") == "SQL mysql://h/d / t"


def test_profile_sent_to_planner_has_no_source(tmp_path):
    conn = csv_conn(tmp_path, [["1", "a"]], name="secret-name.csv")
    llm = FakeLLM([{"columns": [], "notes": []}])
    make_plan(conn, ["r"], llm)
    user = llm.calls[0][0][1][1]
    prof = json.loads(user.split("Column profile (JSON data):\n")[1])
    assert "source" not in prof and "secret-name" not in user


# ------------------------------------------------------------------ F10 Mongo mixed doc/scalar parents

MIXED = [
    {"_id": 1, "name": " a ", "address": {"state": " ny "}},
    {"_id": 2, "name": "b", "address": "n/a"},
    {"_id": 3, "name": "b", "address": None},
    {"_id": 4, "name": "b", "address": ["x"]},
    {"_id": 5, "name": "b", "address": {}},
    {"_id": 6, "name": "b", "address": {"state": "tx"}},
]
MIXED_PLAN = mkplan(("address.state", [
    {"op": "map_from_column", "source_column": "name", "mapping": {"b": "CA"}}, TRIM]))


def _mixed(in_place):
    c = mongomock.MongoClient()
    c.db.c.insert_many([dict(d) for d in MIXED])
    return c


@pytest.mark.parametrize("in_place", [False, True])
def test_mongo_non_document_parent_is_left_alone(tmp_path, in_place):
    c = _mixed(in_place)
    rep = clean(mongo_conn(c), MIXED_PLAN, None, in_place=in_place, run_dir=tmp_path / "r")
    assert rep.status == "done", rep.error
    out = {d["_id"]: d for d in (c.db.c if in_place else c.db.c_cleaned).find()}
    assert out[2]["address"] == "n/a" and out[3]["address"] is None and out[4]["address"] == ["x"]
    assert out[5]["address"] == {"state": "CA"}          # empty document parent: writable
    assert out[6]["address"] == {"state": "CA"} and out[1]["address"] == {"state": "ny"}
    logged = {r[0] for r in read_csv(tmp_path / "r" / "changes.csv")[1:]}
    assert logged == {"1", "5", "6"}                      # nothing claimed for 2, 3, 4
    assert len(out) == 6


def test_mongo_record_marks_unaddressable_paths():
    from scrubber.connectors.base import OPAQUE

    c = mongomock.MongoClient()
    c.db.c.insert_many([{"_id": 1, "address": {"city": "x"}}, {"_id": 2, "address": "n/a"},
                        {"_id": 3, "address": None}, {"_id": 4, "address": {"city": "y"}}])
    conn = mongo_conn(c)
    names = {i.name for i in conn.column_info()}
    assert names == {"address", "address.city"}
    recs = {k: r for b in conn.iter_batches(10) for k, r in b}
    assert recs[1]["address"] is OPAQUE and recs[1]["address.city"] == "x"
    assert recs[2]["address"] == "n/a" and recs[2]["address.city"] is OPAQUE
    assert recs[3]["address"] is None and recs[3]["address.city"] is OPAQUE


def test_opaque_never_reaches_profile():
    c = mongomock.MongoClient()
    c.db.c.insert_many([{"_id": 1, "a": {"b": "x"}}, {"_id": 2, "a": "s"}])
    llm = FakeLLM([{"columns": [], "notes": []}])
    make_plan(mongo_conn(c), ["r"], llm)
    assert "addressable" not in llm.calls[0][0][1][1]


# ------------------------------------------------------------------ F11 planner schema repair

GOOD_PLAN = {"columns": [{"column": "name", "ops": [TRIM]}], "notes": []}
BAD_OP = {"columns": [{"column": "name", "ops": [TRIM, {"op": "case", "mode": "sideways"}]}], "notes": []}


def test_planner_schema_errors_go_through_repair_loop(tmp_path):
    conn = csv_conn(tmp_path, [["1", "a"]])
    llm = FakeLLM([BAD_OP, GOOD_PLAN])
    res = make_plan(conn, ["r"], llm)
    assert res.plan.columns[0].ops[0].op == "trim" and len(llm.calls) == 2
    msg = llm.calls[1][0][-1][1]
    assert "columns[0].ops[1]" in msg and "mode" in msg


def test_planner_schema_errors_end_in_plan_error(tmp_path):
    conn = csv_conn(tmp_path, [["1", "a"]])
    llm = FakeLLM(lambda m, s: BAD_OP)
    with pytest.raises(PlanError) as e:
        make_plan(conn, ["r"], llm)
    assert any("columns[0].ops[1]" in x for x in e.value.errors)
    assert len(llm.calls) == 3          # 1 + PLANNER_MAX_REPAIRS


def test_planner_non_object_json_still_repaired(tmp_path):
    conn = csv_conn(tmp_path, [["1", "a"]])
    llm = FakeLLM(["[1, 2]", GOOD_PLAN])
    assert make_plan(conn, ["r"], llm).plan.columns[0].column == "name"


# ------------------------------------------------------------------ F12 prompts / validation

def test_check_plan_rejects_control_characters_with_hint():
    cols = infos("a")
    for field, op in [("pattern", {"op": "regex_replace", "pattern": "x\x08y"}),
                      ("pattern", {"op": "validate", "pattern": "\x0c", "message": "m"}),
                      ("template", {"op": "reformat", "pattern": "(a)", "template": "\x08"}),
                      ("replacement", {"op": "regex_replace", "pattern": "a", "replacement": "\x07"})]:
        errors, _ = check_plan(mkplan(("a", [op])), cols)
        assert len(errors) == 1 and "control character" in errors[0] and "backslash" in errors[0], op
    for ok in ("a\tb", "a\nb", "a\r\nb"):    # tab / newline / CR are legitimate in patterns
        assert check_plan(mkplan(("a", [{"op": "regex_replace", "pattern": ok}])), cols)[0] == []


def test_planner_prompt_mentions_doubled_backslashes_example_and_profile_fields():
    from scrubber.prompts import PLANNER_SYSTEM

    assert "doubled" in PLANNER_SYSTEM and "\\\\d" in PLANNER_SYSTEM
    assert "Example" in PLANNER_SYSTEM and "nullable" in PLANNER_SYSTEM
    assert "shapes" in PLANNER_SYSTEM and "inferred_type" in PLANNER_SYSTEM


def test_profile_carries_nullable_and_max_length():
    from scrubber.profiler import profile_rows

    ci = infos("a", nullable=False, max_length=8)
    p = profile_rows([{"a": "x"}], ci, "s", 1)
    assert p.columns[0].nullable is False and p.columns[0].max_length == 8
    assert profile_rows([{"a": "x"}], infos("a"), "s", 1).columns[0].nullable is True


def test_must_match_carries_ignore_case_and_date_format(tmp_path):
    plan = mkplan(("d", [
        {"op": "parse_date", "output_format": "%d.%m.%Y"},
        {"op": "validate", "pattern": r"\d\d\.\d\d\.\d{4}", "message": "m", "ignore_case": True},
        {"op": "llm_fix", "instruction": "fix", "when": "if_not_match", "pattern": "x+"}]))
    assert must_match_patterns(plan, "d", 2) == [(r"\d\d\.\d\d\.\d{4}", True), ("x+", False)]
    seen = []
    llm = FakeLLM(lambda m, s: (seen.append(m[-1][1]), {"results": []})[1])
    clean(csv_conn(tmp_path, [["1", "garbage"]], header=("id", "d")), plan, llm, run_dir=tmp_path / "r")
    human = seen[0]
    assert "case-insensitive" in human and "%d.%m.%Y" in human


# ------------------------------------------------------------------ F13 / C4 warnings rule

def _warnings(plan, rows, columns=None):
    from scrubber.planner import dry_run

    cols = columns or infos("a", "b")
    return dry_run(plan, rows, len(rows), cols, False).warnings


def test_no_effect_warnings_only_for_listed_ops():
    rows = [{"a": "x", "b": "y"}, {"a": "z", "b": "w"}]
    noop_ops = [
        {"op": "regex_replace", "pattern": "Q"}, {"op": "reformat", "pattern": "Q", "template": "r"},
        {"op": "translate", "mapping": {"Q": "r"}}, {"op": "map_values", "mapping": {"Q": "r"}},
        {"op": "map_from_column", "source_column": "b", "mapping": {"Q": "r"}},
        {"op": "null_if", "values": ["Q"]}, {"op": "parse_date", "output_format": "%Y"},
    ]
    # parse_date on "x" flags -> not a no-op; use an empty column instead for that one
    for op in noop_ops[:-1]:
        w = [x for x in _warnings(mkplan(("a", [op])), rows) if "no effect" in x]
        assert len(w) == 1 and f"({op['op']})" in w[0], op
    empty = [{"a": "", "b": "y"}]
    assert [x for x in _warnings(mkplan(("a", [noop_ops[-1]])), empty) if "no effect" in x]
    # never warn: trim / collapse_whitespace / case / validate that did nothing
    quiet = [{"op": "trim"}, {"op": "collapse_whitespace"}, {"op": "case", "mode": "lower"},
             {"op": "validate", "pattern": ".*", "message": "m"}]
    assert [x for x in _warnings(mkplan(("a", quiet)), rows) if "no effect" in x] == []
    # an op that flagged something is not a no-op
    flagged = mkplan(("a", [{"op": "map_from_column", "source_column": "b", "mode": "fill_empty",
                             "mapping": {"y": "different"}}]))
    assert [x for x in _warnings(flagged, [{"a": "x", "b": "y"}]) if "no effect" in x] == []


def test_llm_fix_warns_only_when_no_cell_triggers():
    val = {"op": "validate", "pattern": "[a-z]+", "message": "m"}
    plan = mkplan(("a", [val, {"op": "llm_fix", "instruction": "i"}]))
    assert [w for w in _warnings(plan, [{"a": "ok", "b": ""}]) if "no effect" in w and "llm_fix" in w]
    assert not [w for w in _warnings(plan, [{"a": "BAD1", "b": ""}]) if "no effect" in w]
    assert [w for w in _warnings(plan, []) if "no effect" in w] == []


# ------------------------------------------------------------------ F14 fixer context truncation

def test_fixer_context_truncated_but_key_is_not(tmp_path):
    rows = [["1", "x" * 500, "bad"]]
    conn = csv_conn(tmp_path, rows, header=("id", "ctx", "email"))
    plan = mkplan(("email", [{"op": "validate", "pattern": r"[a-z]+@x\.com", "message": "m"},
                             {"op": "llm_fix", "instruction": "fix", "context_columns": ["ctx"]}]))
    seen = []

    def respond(messages, schema):
        items = json.loads(messages[-1][1].split("Items (JSON data):\n")[1])
        seen.extend(items)
        return {"results": [{"id": i["id"], "value": "ok@x.com", "fixed": True} for i in items]}

    rep = clean(conn, plan, FakeLLM(respond), run_dir=tmp_path / "r")
    assert len(seen[0]["context"]["ctx"]) == 300
    assert rep.llm.cells_fixed == 1       # answer was matched back to the full-length key


# ------------------------------------------------------------------ F17 CSV

def test_single_column_csv_keeps_blank_lines(tmp_path):
    p = tmp_path / "one.csv"
    p.write_text("name\n a \n\nb\n\n", encoding="utf-8")
    conn = open_source(CSVSource(path=str(p)))
    assert conn.count() == 4
    assert [r["name"] for b in conn.iter_batches(10) for _, r in b] == [" a ", "", "b", ""]
    rep = clean(conn, mkplan(("name", [TRIM])), None, run_dir=tmp_path / "r")
    assert rep.rows == 4 and rep.rows_written == 4
    assert len(read_csv(tmp_path / "r" / "one.cleaned.csv")) == 5


def test_multi_column_csv_still_skips_blank_lines(tmp_path):
    p = tmp_path / "two.csv"
    p.write_text("a,b\n1,2\n\n3,4\n", encoding="utf-8")
    assert open_source(CSVSource(path=str(p))).count() == 2


def test_csv_header_named_extra_rejected(tmp_path):
    p = tmp_path / "x.csv"
    p.write_text("a,__extra__\n1,2\n", encoding="utf-8")
    with pytest.raises(SourceError, match="__extra__"):
        open_source(CSVSource(path=str(p)))


# ------------------------------------------------------------------ F21 simplifications

def test_plan_has_llm_fix():
    assert not mkplan(("a", [TRIM])).has_llm_fix()
    assert mkplan(("a", [{"op": "llm_fix", "instruction": "i"}])).has_llm_fix()
    assert not mkplan(("a", [{"op": "llm_fix", "instruction": "i", "enabled": False}])).has_llm_fix()


def test_map_lookup_cache_does_not_affect_equality_dump_or_copy():
    from scrubber.ops import op_map_values

    mk = lambda: mkplan(("a", [{"op": "map_values", "mapping": {"Ab": "1"}}]))  # noqa: E731
    p1, p2 = mk(), mk()
    assert op_map_values(p1.columns[0].ops[0], "AB") == "1"      # populates the cache
    assert p1 == p2 and p1.model_dump() == p2.model_dump()
    assert "folded" not in p1.model_dump_json()
    clone = p1.model_copy(deep=True)
    assert op_map_values(clone.columns[0].ops[0], "aB") == "1"
    # no module-level id()-keyed cache any more
    import scrubber.ops as ops_mod

    assert not hasattr(ops_mod, "_fold_cache")
