from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from test_sql_connector import make_db
from typer.testing import CliRunner
from util import approved, write_csv

import scrubber.cli as cli
from scrubber.cleaner import RunReport
from scrubber.connectors import CSVSource, MongoSource, SQLSource
from scrubber.llm import FakeLLM, LLMError

DATA = Path(__file__).parent / "data"
runner = CliRunner()
TRIM = {"op": "trim"}
PLAN = {"columns": [{"column": "name", "ops": [TRIM]}]}


def invoke(*args, **kw):
    return runner.invoke(cli.app, [str(a) for a in args], **kw)


@pytest.fixture
def csv_file(tmp_path):
    return write_csv(tmp_path / "in.csv", [["id", "name"], ["1", " a "], ["2", "b"]])


@pytest.fixture
def plan_file(tmp_path):
    p = tmp_path / "plan.json"
    p.write_text(json.dumps(approved(PLAN)))
    return p


def spec(source, **kw):
    d = dict(delimiter=",", encoding="utf-8-sig", table=None, schema=None, key_column=None,
             db=None, collection=None, filter_=None)
    d.update(kw)
    return cli.build_spec(source, **d)


# ---------------------------------------------------------------- SOURCE detection


def test_source_detection(monkeypatch):
    assert isinstance(spec("data.csv"), CSVSource)
    assert spec("data.tsv", delimiter="\\t").delimiter == "\t"
    s = spec("sqlite:///x.db", table="t", schema="s", key_column="k")
    assert isinstance(s, SQLSource) and s.table == "t" and s.db_schema == "s" and s.key_column == "k"
    m = spec("mongodb://h/", db="d", collection="c", filter_='{"a": 1}')
    assert isinstance(m, MongoSource) and m.filter == {"a": 1}
    assert isinstance(spec("mongodb+srv://h/", db="d", collection="c"), MongoSource)
    monkeypatch.setenv("MY_URL", "postgresql://u:p@h/db")
    s = spec("env:MY_URL", table="t")
    assert isinstance(s, SQLSource) and s.url == "postgresql://u:p@h/db"


def test_source_usage_errors(monkeypatch):
    import typer

    monkeypatch.delenv("NOPE_URL", raising=False)
    for kwargs in (dict(source="sqlite:///x.db"), dict(source="mongodb://h/", db="d"),
                   dict(source="mongodb://h/", collection="c"), dict(source="env:NOPE_URL"),
                   dict(source="mongodb://h/", db="d", collection="c", filter_="{bad"),
                   dict(source="mongodb://h/", db="d", collection="c", filter_="[1]")):
        src = kwargs.pop("source")
        with pytest.raises(typer.Exit) as e:
            spec(src, **kwargs)
        assert e.value.exit_code == 2


def test_cli_missing_options_exit_2(tmp_path):
    assert invoke("profile", "sqlite:///x.db").exit_code == 2
    assert invoke("profile", "mongodb://h/", "--db", "d").exit_code == 2
    r = invoke("profile", "env:UNSET_VAR_XYZ")
    assert r.exit_code == 2
    assert invoke("apply", "x.csv").exit_code == 2  # --plan required by click -> usage error


# ---------------------------------------------------------------- profile / apply


def test_profile_csv(csv_file):
    r = invoke("profile", csv_file)
    assert r.exit_code == 0 and "name" in r.stdout and "rows: 2" in r.stdout


def test_missing_csv_is_failure_exit_1(tmp_path):
    r = invoke("profile", tmp_path / "nope.csv")
    assert r.exit_code == 1 and "not found" in r.output


def test_apply_no_llm_end_to_end_golden(tmp_path):
    out = tmp_path / "out"
    plan = tmp_path / "leads_plan.json"
    plan.write_text(json.dumps(approved(json.loads((DATA / "leads_plan.json").read_text()))))
    r = invoke("apply", DATA / "leads.csv", "--plan", plan, "--no-llm", "--out", out)
    assert r.exit_code == 0, r.output
    (run_dir,) = list(out.iterdir())
    assert sorted(p.name for p in run_dir.iterdir()) == [
        "changes.csv", "flags.csv", "leads.cleaned.csv", "plan.json", "report.json"]
    assert (run_dir / "leads.cleaned.csv").read_text() == (DATA / "leads_expected.csv").read_text()
    rep = json.loads((run_dir / "report.json").read_text())
    assert rep["status"] == "done" and rep["rows"] == 50
    assert "50 read, 50 written" in r.stdout


def test_apply_plan_errors(tmp_path, csv_file, plan_file):
    assert invoke("apply", csv_file, "--plan", tmp_path / "missing.json").exit_code == 2
    bad = tmp_path / "bad.json"
    bad.write_text('{"columns": [{"column": "name", "ops": [{"op": "nope"}]}]}')
    r = invoke("apply", csv_file, "--plan", bad, "--out", tmp_path / "o")
    assert r.exit_code == 1 and "invalid plan" in r.output
    unk = tmp_path / "unk.json"
    unk.write_text(json.dumps(approved({"columns": [{"column": "zzz", "ops": [TRIM]}]})))
    r = invoke("apply", csv_file, "--plan", unk, "--out", tmp_path / "o")
    assert r.exit_code == 1 and "zzz" in r.output
    assert not (tmp_path / "o").exists()


def test_apply_csv_in_place_fails(tmp_path, csv_file, plan_file):
    r = invoke("apply", csv_file, "--plan", plan_file, "--in-place", "--out", tmp_path / "o")
    assert r.exit_code == 1 and "in-place" in r.output


LLM_PLAN = {"columns": [{"column": "name", "ops": [
    {"op": "validate", "pattern": "[a-z]+", "message": "bad"},
    {"op": "llm_fix", "instruction": "fix"}]}]}


def test_make_llm_only_built_when_needed(tmp_path, csv_file, monkeypatch):
    calls = []

    def fake_make(planner=False):
        calls.append(planner)
        raise LLMError("OPENAI_API_KEY is not set")

    monkeypatch.setattr(cli, "make_llm", fake_make)
    p = tmp_path / "llm.json"
    p.write_text(json.dumps(approved(LLM_PLAN)))
    r = invoke("apply", csv_file, "--plan", p, "--no-llm", "--out", tmp_path / "o1")
    assert r.exit_code == 0 and calls == []
    r = invoke("apply", csv_file, "--plan", p, "--out", tmp_path / "o2")
    assert r.exit_code == 1 and "OPENAI_API_KEY" in r.output and calls == [False]
    plain = tmp_path / "plain.json"
    plain.write_text(json.dumps(approved(PLAN)))
    assert invoke("apply", csv_file, "--plan", plain, "--out", tmp_path / "o3").exit_code == 0
    assert calls == [False]


def test_apply_exit_codes_for_failed_and_cancelled(tmp_path, csv_file, plan_file, monkeypatch):
    for status, code in (("failed", 1), ("cancelled", 130)):
        monkeypatch.setattr(cli, "clean", lambda *a, _s=status, **k: RunReport(
            run_id="x", status=_s, error="boom" if _s == "failed" else None))
        r = invoke("apply", csv_file, "--plan", plan_file, "--out", tmp_path / "o")
        assert r.exit_code == code, r.output


def test_apply_sql_new_table_exists_overwrite_and_in_place(tmp_path):
    url = make_db(tmp_path)
    p = tmp_path / "p.json"
    p.write_text(json.dumps(approved({"columns": [{"column": "email", "ops": [TRIM]}]})))
    base = ["apply", url, "--table", "people", "--plan", p, "--out", tmp_path / "o"]
    r = invoke(*base)
    assert r.exit_code == 0, r.output
    r = invoke(*base)
    assert r.exit_code == 1 and "already exists" in r.output
    assert invoke(*base, "--overwrite").exit_code == 0
    # in-place: typed confirmation
    r = invoke(*base, "--in-place", input="wrong\n")
    assert r.exit_code == 1 and "aborted" in r.output
    con = sqlite3.connect(url.removeprefix("sqlite:///"))
    assert con.execute("SELECT email FROM people WHERE id=1").fetchone()[0] == " A@X.com "
    r = invoke(*base, "--in-place", input="people\n")
    assert r.exit_code == 0, r.output
    assert con.execute("SELECT email FROM people WHERE id=1").fetchone()[0] == "A@X.com"
    con.execute("UPDATE people SET email=' q '")
    con.commit()
    assert invoke(*base, "--in-place", "--yes").exit_code == 0
    assert con.execute("SELECT email FROM people WHERE id=1").fetchone()[0] == "q"


def test_secrets_not_printed(tmp_path):
    r = invoke("profile", "postgresql://user:hunter2@127.0.0.1:1/db", "--table", "t")
    assert r.exit_code == 1 and "hunter2" not in r.output


# ---------------------------------------------------------------- plan / run


def planner_llm(monkeypatch, cols=None):
    llm = FakeLLM([{"columns": cols or PLAN["columns"], "notes": ["a note"]}])
    seen = []

    def fake_make(planner=False):
        seen.append(planner)
        return llm

    monkeypatch.setattr(cli, "make_llm", fake_make)
    return llm, seen


def test_rules_file_parsing(tmp_path):
    f = tmp_path / "r.txt"
    f.write_text("# comment\n\n  Trim names \n#another\nSecond rule\n", encoding="utf-8")
    assert cli.read_rules(f) == ["Trim names", "Second rule"]
    f.write_text("# only comments\n\n")
    import typer

    with pytest.raises(typer.Exit):
        cli.read_rules(f)


def test_plan_command(tmp_path, csv_file, monkeypatch):
    llm, seen = planner_llm(monkeypatch)
    rules = tmp_path / "rules.txt"
    rules.write_text("# c\nTrim names\n")
    out = tmp_path / "plan.json"
    r = invoke("plan", csv_file, "--rules", rules, "-o", out)
    assert r.exit_code == 0, r.output
    assert seen == [True]  # planner model
    plan = json.loads(out.read_text())
    assert plan["rules"] == ["Trim names"] and plan["notes"] == ["a note"]
    assert "Plan" in r.stdout and "trim" in r.stdout and "Preview" in r.stdout
    assert "a note" in r.stdout and "tokens" in r.stdout


def test_plan_errors(tmp_path, csv_file, monkeypatch):
    rules = tmp_path / "rules.txt"
    rules.write_text("x\n")
    planner_llm(monkeypatch)
    r = invoke("plan", csv_file, "--rules", rules, "--columns", "zzz", "-o", tmp_path / "p.json")
    assert r.exit_code == 2 and "zzz" in r.output
    assert invoke("plan", csv_file, "--rules", tmp_path / "none.txt").exit_code == 2

    def boom(planner=False):
        raise LLMError("no key")

    monkeypatch.setattr(cli, "make_llm", boom)
    r = invoke("plan", csv_file, "--rules", rules, "-o", tmp_path / "p.json")
    assert r.exit_code == 1 and "no key" in r.output and not (tmp_path / "p.json").exists()

    bad = {"columns": [{"column": "nope", "ops": [TRIM]}], "notes": []}
    monkeypatch.setattr(cli, "make_llm", lambda planner=False: FakeLLM(lambda m, s: bad))
    r = invoke("plan", csv_file, "--rules", rules, "-o", tmp_path / "p.json")
    assert r.exit_code == 1 and "nope" in r.output


def test_run_command(tmp_path, csv_file, monkeypatch):
    rules = tmp_path / "rules.txt"
    rules.write_text("Trim names\n")
    planner_llm(monkeypatch)
    out = tmp_path / "out"
    r = invoke("run", csv_file, "--rules", rules, "--out", out, input="Ann\nn\n")
    assert r.exit_code == 1 and "Approve this plan as Ann and apply?" in r.stdout and not out.exists()
    planner_llm(monkeypatch)
    r = invoke("run", csv_file, "--rules", rules, "--out", out, input="Ann\ny\n")
    assert r.exit_code == 0, r.output
    (run_dir,) = list(out.iterdir())
    assert (run_dir / "in.cleaned.csv").read_text().splitlines()[1] == "1,a"
    assert json.loads((run_dir / "report.json").read_text())["approval"]["by"] == "Ann"
    planner_llm(monkeypatch)
    r = invoke("run", csv_file, "--rules", rules, "--out", tmp_path / "out2", "--yes",
               "--approved-by", "Bo")
    assert r.exit_code == 0 and "apply?" not in r.stdout


# ---------------------------------------------------------------- serve


def test_serve_refuses_remote_host(monkeypatch):
    import uvicorn

    called = {}
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: called.update(kw, app=app))
    r = invoke("serve", "--host", "0.0.0.0")
    assert r.exit_code == 2 and "--allow-remote" in r.output and not called
    r = invoke("serve", "--port", "8123")
    assert r.exit_code == 0 and called["host"] == "127.0.0.1" and called["port"] == 8123
    r = invoke("serve", "--host", "0.0.0.0", "--allow-remote", "--port", "8124")
    assert r.exit_code == 0 and "NO authentication" in r.output and called["host"] == "0.0.0.0"
    # the app was built with that port for the Host check
    from fastapi.testclient import TestClient

    with TestClient(called["app"], base_url="http://10.1.1.1:8124") as c:
        assert c.get("/api/config").status_code == 200  # allow_remote disables the Host check
