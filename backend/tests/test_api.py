from __future__ import annotations

import io
import time
from pathlib import Path

import mongomock
import pytest
from fastapi.testclient import TestClient
from test_sql_connector import make_db
from util import approved

from scrubber.api import create_app
from scrubber.config import get_settings
from scrubber.llm import FakeLLM, LLMError

CSV = b"id,name,email\n1, Ann ,A@X.com\n2,bob,b@x.com\n3, cy ,bad\n"
TRIM = {"op": "trim"}
VAL = {"op": "validate", "pattern": r"[a-z.]+@[a-z]+\.com", "message": "Invalid email"}
PLAN = {"columns": [{"column": "name", "ops": [TRIM]},
                    {"column": "email", "ops": [TRIM, {"op": "case", "mode": "lower"}, VAL]}]}
BASE = "http://127.0.0.1:8000"


def make_client(llm=None, **kw):
    app = create_app(port=8000, llm_factory=(lambda planner: llm) if llm is not None else None, **kw)
    return TestClient(app, base_url=BASE)


@pytest.fixture
def client():
    with make_client() as c:
        yield c


def upload(c, data=CSV, name="my leads!.csv", **form):
    return c.post("/api/uploads", files={"file": (name, io.BytesIO(data), "text/csv")}, data=form)


def wait(c, run_id, timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        st = c.get(f"/api/runs/{run_id}").json()
        if st["status"] in ("done", "failed", "cancelled"):
            return st
        time.sleep(0.02)
    raise AssertionError("run did not finish")


def test_config(client, monkeypatch):
    j = client.get("/api/config").json()
    assert set(j) == {"model", "planner_model", "has_api_key", "sample_size", "max_llm_cells", "max_upload_mb"}
    assert j["model"] == "openai:gpt-5-mini" and j["planner_model"] == "openai:gpt-6-luna" and j["sample_size"] == 1000


def test_full_flow_csv(client):
    r = upload(client)
    assert r.status_code == 200
    src = r.json()
    assert src["kind"] == "csv" and src["can_in_place"] is False and src["count"] == 3
    assert src["description"] == "CSV my_leads_.csv"
    assert [c["name"] for c in src["columns"]] == ["id", "name", "email"]
    assert "path" not in str(src)
    sid = src["source_id"]

    prof = client.get(f"/api/sources/{sid}/profile").json()
    assert prof["sampled_rows"] == 3 and prof["columns"][0]["name"] == "id"

    pv = client.post("/api/preview", json={"source_id": sid, "plan": PLAN}).json()["preview"]
    assert pv["sampled_rows"] == 3 and pv["columns"][0]["column"] == "name"

    run_id = client.post("/api/runs", json={"source_id": sid, "plan": approved(PLAN)}).json()["run_id"]
    st = wait(client, run_id)
    assert st["status"] == "done" and st["report"]["rows"] == 3
    assert set(st["files"]) == {"changes.csv", "flags.csv", "my_leads_.cleaned.csv", "plan.json",
                                "report.json"}

    ch = client.get(f"/api/runs/{run_id}/changes").json()
    assert ch["total"] == st["report"]["cells_changed"] == len(ch["items"]) == 3
    assert {"row_key": "0", "column": "name", "before": " Ann ", "after": "Ann",
            "sources": ["trim"]} in ch["items"]
    assert client.get(f"/api/runs/{run_id}/changes?column=name").json()["total"] == 2
    page = client.get(f"/api/runs/{run_id}/changes?offset=1&limit=2").json()
    assert page["total"] == 3 and len(page["items"]) == 2
    fl = client.get(f"/api/runs/{run_id}/flags").json()
    assert fl["total"] == 1 and fl["items"][0] == {
        "row_key": "2", "column": "email", "value": "bad", "message": "Invalid email"}
    assert client.get(f"/api/runs/{run_id}/flags?column=name").json() == {"total": 0, "items": []}

    f = client.get(f"/api/runs/{run_id}/files/my_leads_.cleaned.csv")
    assert f.status_code == 200 and "Ann,a@x.com" in f.text
    assert client.post(f"/api/runs/{run_id}/cancel").json()["status"] == "done"


def test_plan_endpoint_with_fake_llm():
    llm = FakeLLM([{"columns": PLAN["columns"], "notes": ["n"]}])
    with make_client(llm) as c:
        sid = upload(c).json()["source_id"]
        r = c.post("/api/plan", json={"source_id": sid, "rules": ["Trim names"]})
        assert r.status_code == 200
        j = r.json()
        assert j["plan"]["rules"] == ["Trim names"] and j["plan"]["notes"] == ["n"]
        assert j["preview"]["sampled_rows"] == 3 and j["usage"]["calls"] == 1
        assert c.post("/api/plan", json={"source_id": sid, "rules": []}).status_code == 400
        assert c.post("/api/plan", json={"source_id": sid, "rules": ["x"],
                                         "columns": ["zzz"]}).status_code == 400


def test_plan_llm_errors():
    with make_client(FakeLLM([LLMError("provider down")])) as c:
        sid = upload(c).json()["source_id"]
        r = c.post("/api/plan", json={"source_id": sid, "rules": ["x"]})
        assert r.status_code == 502 and "provider down" in r.json()["detail"]

    def boom(planner):
        raise LLMError("OPENAI_API_KEY is not set")

    with TestClient(create_app(port=8000, llm_factory=boom), base_url=BASE) as c:
        sid = upload(c).json()["source_id"]
        r = c.post("/api/plan", json={"source_id": sid, "rules": ["x"]})
        assert r.status_code == 400 and "OPENAI_API_KEY" in r.json()["detail"]


def test_plan_repair_failure_is_422():
    bad = {"columns": [{"column": "nope", "ops": [TRIM]}], "notes": []}
    with make_client(FakeLLM(lambda m, s: bad)) as c:
        sid = upload(c).json()["source_id"]
        r = c.post("/api/plan", json={"source_id": sid, "rules": ["x"]})
        assert r.status_code == 422 and any("nope" in e for e in r.json()["errors"])


def test_invalid_plan_422_with_errors(client):
    sid = upload(client).json()["source_id"]
    for url in ("/api/preview", "/api/runs"):
        r = client.post(url, json={"source_id": sid, "plan": approved(
            {"columns": [{"column": "zz", "ops": [TRIM]}]})})
        assert r.status_code == 422 and r.json()["errors"] and "zz" in r.json()["errors"][0]
        r = client.post(url, json={"source_id": sid, "plan": {"columns": [
            {"column": "name", "ops": [{"op": "trim", "bogus": 1}]}]}})
        assert r.status_code == 422 and "bogus" in " ".join(r.json()["errors"])
    r = client.post("/api/runs", json={"plan": {}})  # FastAPI's own validation error
    assert r.status_code == 422 and r.json()["detail"] and r.json()["errors"]


def test_unknown_source_and_run(client):
    assert client.get("/api/sources/nope/profile").status_code == 404
    assert client.post("/api/runs", json={"source_id": "nope", "plan": {}}).status_code == 404
    assert client.get("/api/runs/20200101-000000-abcd").status_code == 404
    assert client.get("/api/nothing").json() == {"detail": "Not found"}


def test_csv_in_place_rejected(client):
    sid = upload(client).json()["source_id"]
    r = client.post("/api/runs", json={"source_id": sid, "plan": approved(PLAN), "in_place": True})
    assert r.status_code == 400


def test_sql_source_flow_409_overwrite_in_place(tmp_path):
    url = make_db(tmp_path)
    with make_client() as c:
        r = c.post("/api/sources", json={"kind": "sql", "url": url, "table": "people"})
        assert r.status_code == 200, r.text
        src = r.json()
        assert src["can_in_place"] and src["count"] == 4 and "url" not in src
        sid = src["source_id"]
        plan = {"columns": [{"column": "email", "ops": [TRIM]}]}
        run = c.post("/api/runs", json={"source_id": sid, "plan": approved(plan)}).json()["run_id"]
        assert wait(c, run)["status"] == "done"
        r = c.post("/api/runs", json={"source_id": sid, "plan": approved(plan)})
        assert r.status_code == 409 and "overwrite" in r.json()["detail"]
        run = c.post("/api/runs", json={"source_id": sid, "plan": approved(plan), "overwrite": True}).json()["run_id"]
        assert wait(c, run)["status"] == "done"
        # in-place; key column may not be planned
        r = c.post("/api/runs", json={"source_id": sid, "plan": approved({"columns": [
            {"column": "name", "ops": [TRIM]}]}), "in_place": True})
        st = wait(c, r.json()["run_id"])
        assert st["status"] == "done" and st["report"]["in_place"]
        assert st["files"] == ["changes.csv", "flags.csv", "plan.json", "report.json"]
    # bad credentials / table never echoed
    with make_client() as c:
        r = c.post("/api/sources", json={"kind": "sql", "url": "postgresql://u:s3cret@127.0.0.1:1/d",
                                         "table": "t"})
        assert r.status_code == 400 and "s3cret" not in r.text
        r = c.post("/api/sources", json={"kind": "sql", "url": url, "table": "missing"})
        assert r.status_code == 400
        r = c.post("/api/sources", json={"kind": "csv", "path": "/etc/passwd"})
        assert r.status_code == 422


def test_mongo_source(tmp_path):
    m = mongomock.MongoClient()
    m.db.c.insert_many([{"_id": 1, "name": " a "}, {"_id": 2, "name": "b"}])
    with make_client(mongo_client=m) as c:
        r = c.post("/api/sources", json={"kind": "mongo", "uri": "mongodb://u:pw@h/", "database": "db",
                                         "collection": "c"})
        assert r.status_code == 200 and r.json()["columns"][0]["name"] == "name"
        assert "pw" not in r.text
        sid = r.json()["source_id"]
        run = c.post("/api/runs", json={"source_id": sid, "plan": approved({"columns": [
            {"column": "name", "ops": [TRIM]}]})}).json()["run_id"]
        assert wait(c, run)["status"] == "done"
        assert m.db.c_cleaned.find_one({"_id": 1})["name"] == "a"
        ch = c.get(f"/api/runs/{run}/changes").json()
        assert ch["items"][0]["row_key"] == "1"


def test_llm_fix_run_uses_injected_llm():
    def fix(messages, schema):
        import json
        items = json.loads(messages[-1][1].split("Items (JSON data):\n")[1])
        return {"results": [{"id": i["id"], "value": "z@x.com", "fixed": True} for i in items]}

    plan = {"columns": [{"column": "email", "ops": [TRIM, VAL, {"op": "llm_fix", "instruction": "fix"}]}]}
    llm = FakeLLM(fix)
    with make_client(llm) as c:
        sid = upload(c).json()["source_id"]
        run = c.post("/api/runs", json={"source_id": sid, "plan": approved(plan)}).json()["run_id"]
        st = wait(c, run)
        assert st["status"] == "done" and st["report"]["llm"]["cells_fixed"] == 2
        # no_llm needs no LLM at all
        run = c.post("/api/runs", json={"source_id": sid, "plan": approved(plan), "no_llm": True}).json()["run_id"]
        assert wait(c, run)["report"]["llm"]["calls"] == 0

    def boom(planner):
        raise LLMError("no key")

    with TestClient(create_app(port=8000, llm_factory=boom), base_url=BASE) as c:
        sid = upload(c).json()["source_id"]
        assert c.post("/api/runs", json={"source_id": sid, "plan": approved(plan)}).status_code == 400
        run = c.post("/api/runs", json={"source_id": sid, "plan": approved(plan), "no_llm": True}).json()["run_id"]
        assert wait(c, run)["status"] == "done"


def test_host_and_origin_checks():
    app = create_app(port=8000)
    with TestClient(app, base_url="http://evil.example:8000") as c:
        r = c.get("/api/config")
        assert r.status_code == 403 and "detail" in r.json()
        assert c.get("/").status_code == 200  # static is not guarded
    with TestClient(app, base_url=BASE) as c:
        assert c.get("/api/config").status_code == 200
        assert c.get("/api/config", headers={"Origin": BASE}).status_code == 200
        r = c.get("/api/config", headers={"Origin": "http://evil.example"})
        assert r.status_code == 403
        assert c.get("/api/config", headers={"Origin": "http://localhost:5173"}).status_code == 403
        assert "access-control-allow-origin" not in c.get("/api/config", headers={"Origin": BASE}).headers
    for host in ("localhost:8000", "[::1]:8000"):
        with TestClient(app, base_url=f"http://{host}") as c:
            assert c.get("/api/config").status_code == 200
    with TestClient(app, base_url="http://127.0.0.1:9999") as c:
        assert c.get("/api/config").status_code == 403


def test_dev_and_allow_remote_modes():
    with TestClient(create_app(port=8000, dev=True), base_url=BASE) as c:
        assert c.get("/api/config", headers={"Origin": "http://localhost:5173"}).status_code == 200
    with TestClient(create_app(port=8000, dev=True), base_url="http://localhost:5173") as c:
        assert c.get("/api/config").status_code == 200  # Vite proxy keeps its Host
    with TestClient(create_app(port=8000, allow_remote=True), base_url="http://10.0.0.5:8000") as c:
        assert c.get("/api/config").status_code == 200  # Host check off
        assert c.get("/api/config", headers={"Origin": "http://evil.example"}).status_code == 403
        assert c.get("/api/config", headers={"Origin": "http://10.0.0.5:8000"}).status_code == 200


def test_run_id_and_file_name_validation(client):
    sid = upload(client).json()["source_id"]
    run = client.post("/api/runs", json={"source_id": sid, "plan": approved(PLAN)}).json()["run_id"]
    wait(client, run)
    for bad in ("abc", "20200101-000000-ABCD", "%2e%2e"):
        assert client.get(f"/api/runs/{bad}").status_code == 404
        assert client.get(f"/api/runs/{bad}/changes").status_code == 404
        assert client.get(f"/api/runs/{bad}/files/report.json").status_code == 404
    for name in ("..%2fother", "%2e%2e%2freport.json", "nope.csv", "%2e%2e", "plan.json%00",
                 "%2fetc%2fpasswd"):
        assert client.get(f"/api/runs/{run}/files/{name}").status_code == 404
    assert client.get(f"/api/runs/{run}/files/report.json").status_code == 200


def test_upload_limits_and_errors(monkeypatch):
    monkeypatch.setenv("MAX_UPLOAD_MB", "1")
    get_settings.cache_clear()
    with make_client() as c:
        big = b"a,b\n" + b"x,y\n" * 400_000  # ~1.6 MB
        r = upload(c, big)
        assert r.status_code == 413 and "limit" in r.json()["detail"]
        assert not list(Path(get_settings().upload_dir).glob("*.csv"))  # nothing left behind
        r = upload(c, b"", name="e.csv")
        assert r.status_code == 400
        r = upload(c, b"a,a\n1,2\n")
        assert r.status_code == 400 and "duplicate" in r.json()["detail"]
        r = upload(c, b"a;b\n1;2\n", delimiter=";")
        assert r.status_code == 200 and len(r.json()["columns"]) == 2
        r = upload(c, b"a\n1\n", delimiter="ab")
        assert r.status_code == 400
        r = upload(c, "a\n\xe9\n".encode("latin-1"))
        assert r.status_code == 400 and "encoding" in r.json()["detail"]
        assert upload(c, "a\n\xe9\n".encode("latin-1"), encoding="latin-1").status_code == 200


def test_finished_run_readable_after_restart():
    with make_client() as c:
        sid = upload(c).json()["source_id"]
        run = c.post("/api/runs", json={"source_id": sid, "plan": approved(PLAN)}).json()["run_id"]
        wait(c, run)
    with make_client() as c2:  # new process: empty registries
        st = c2.get(f"/api/runs/{run}").json()
        assert st["status"] == "done" and st["report"]["rows"] == 3 and "report.json" in st["files"]
        assert c2.get(f"/api/runs/{run}/changes").json()["total"] == 3


def test_cancel_queued_run(monkeypatch):
    import threading

    gate = threading.Event()
    import scrubber.runs as runs_mod

    real = runs_mod.clean

    def slow_clean(*a, **kw):
        gate.wait(5)
        return real(*a, **kw)

    monkeypatch.setattr(runs_mod, "clean", slow_clean)
    with make_client() as c:
        sid = upload(c).json()["source_id"]
        r1 = c.post("/api/runs", json={"source_id": sid, "plan": approved(PLAN)}).json()["run_id"]
        r2 = c.post("/api/runs", json={"source_id": sid, "plan": approved(PLAN)}).json()["run_id"]
        assert c.get(f"/api/runs/{r2}").json()["status"] == "queued"
        assert c.post(f"/api/runs/{r2}/cancel").status_code == 200
        gate.set()
        assert wait(c, r1)["status"] == "done"
        assert wait(c, r2)["status"] == "cancelled"


def test_static_fallback_and_spa(tmp_path, monkeypatch):
    monkeypatch.setenv("WEB_DIST", str(tmp_path / "missing"))
    get_settings.cache_clear()
    with make_client() as c:
        r = c.get("/")
        assert r.status_code == 200 and "npm run build" in r.text
        assert c.get("/api/config").status_code == 200
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html>INDEX</html>")
    (dist / "assets" / "a.js").write_text("console.log(1)")
    (tmp_path / "secret.txt").write_text("secret")
    monkeypatch.setenv("WEB_DIST", str(dist))
    get_settings.cache_clear()
    with make_client() as c:
        assert "INDEX" in c.get("/").text and "INDEX" in c.get("/some/route").text
        assert c.get("/assets/a.js").text == "console.log(1)"
        assert "INDEX" in c.get("/..%2fsecret.txt").text and "secret" not in c.get("/%2e%2e/secret.txt").text
