"""Plan approval (spec §4.5): hash, API endpoints/enforcement, CLI approve/apply/run."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner
from util import approved, mkplan, write_csv

import scrubber.cli as cli
from scrubber.cleaner import clean
from scrubber.connectors import CSVSource, open_source
from scrubber.plan import Plan, approval_error

from test_api import CSV, PLAN, make_client, upload, wait  # noqa: E402
from test_cli import planner_llm  # noqa: E402

runner = CliRunner()
TRIM = {"op": "trim"}
SMALL = {"columns": [{"column": "name", "ops": [TRIM]}]}


def invoke(*args, **kw):
    return runner.invoke(cli.app, [str(a) for a in args], **kw)


@pytest.fixture
def csv_file(tmp_path):
    return write_csv(tmp_path / "in.csv", [["id", "name"], ["1", " a "], ["2", "b"]])


def write_plan(tmp_path, plan: dict, name="plan.json") -> Path:
    p = tmp_path / name
    p.write_text(json.dumps(plan))
    return p


# ------------------------------------------------------------------ model


def test_hash_stable_across_dump_load_and_key_order():
    p = mkplan(("name", [TRIM, {"op": "case", "mode": "lower"}]))
    ap = p.with_approval("Ann")
    again = Plan.model_validate_json(ap.model_dump_json())
    assert again.content_hash() == p.content_hash() == ap.approval.plan_hash
    assert again.is_approved()
    d = p.model_dump(mode="json")
    reordered = Plan.model_validate(dict(reversed(list(d.items()))))
    assert reordered.content_hash() == p.content_hash()


def test_any_edit_voids_approval():
    ap = mkplan(("name", [TRIM])).with_approval("Ann")
    assert ap.is_approved() and approval_error(ap) is None
    edited = ap.model_copy(deep=True)
    edited.columns[0].ops[0].enabled = False
    assert not edited.is_approved()
    assert approval_error(edited) == ("Plan changed after it was approved by Ann. "
                                      "Approve it again.")
    renamed = ap.model_copy(update={"rules": ["new rule"]})
    assert not renamed.is_approved()
    assert approval_error(mkplan(("name", [TRIM]))) == "Plan is not approved. Approve it before running."


def test_with_approval_name_validation():
    p = mkplan(("name", [TRIM]))
    for bad in ("", "   ", "x" * 101):
        with pytest.raises(ValueError):
            p.with_approval(bad)
    a = p.with_approval("  Ann  ").approval
    assert a.by == "Ann" and a.at.tzinfo is not None


def test_clean_does_not_enforce_approval(tmp_path, csv_file):
    rep = clean(open_source(CSVSource(path=str(csv_file))), mkplan(("name", [TRIM])), None,
                run_dir=tmp_path / "r")
    assert rep.status == "done" and rep.approval is None


# ------------------------------------------------------------------ API


def test_approve_endpoint():
    with make_client() as c:
        r = c.post("/api/plans/approve", json={"plan": SMALL, "by": " Ann "})
        assert r.status_code == 200
        pl = r.json()["plan"]
        assert pl["approval"]["by"] == "Ann" and pl["approval"]["at"]
        assert pl["approval"]["plan_hash"] == Plan.model_validate(SMALL).content_hash()
        assert Plan.model_validate(pl).is_approved()
        r = c.post("/api/plans/approve", json={"plan": SMALL, "by": "  "})
        assert r.status_code == 422 and "detail" in r.json()
        r = c.post("/api/plans/approve", json={"plan": {"columns": [{"column": "n", "ops": [
            {"op": "bogus"}]}]}, "by": "Ann"})
        assert r.status_code == 422 and r.json()["errors"]


def test_preview_drops_stale_approval_and_keeps_valid():
    with make_client() as c:
        sid = upload(c).json()["source_id"]
        good = approved(PLAN)
        j = c.post("/api/preview", json={"source_id": sid, "plan": good}).json()
        assert j["plan"]["approval"] == good["approval"]
        assert not any("Approval by" in w for w in j["preview"]["warnings"])
        stale = json.loads(json.dumps(good))
        stale["columns"][0]["ops"][0]["enabled"] = False
        j = c.post("/api/preview", json={"source_id": sid, "plan": stale}).json()
        assert j["plan"]["approval"] is None
        assert ("Approval by Tester was removed: the plan changed after it was approved."
                in j["preview"]["warnings"])
        j = c.post("/api/preview", json={"source_id": sid, "plan": PLAN}).json()
        assert j["plan"]["approval"] is None
        assert not any("Approval by" in w for w in j["preview"]["warnings"])


def test_runs_409_for_unapproved_and_stale_then_success_and_report():
    with make_client() as c:
        sid = upload(c).json()["source_id"]
        r = c.post("/api/runs", json={"source_id": sid, "plan": PLAN})
        assert r.status_code == 409
        assert r.json() == {"detail": "Plan is not approved. Approve it before running."}
        stale = approved(PLAN, "Ann")
        stale["columns"][0]["ops"][0]["enabled"] = False
        r = c.post("/api/runs", json={"source_id": sid, "plan": stale})
        assert r.status_code == 409
        assert r.json()["detail"] == "Plan changed after it was approved by Ann. Approve it again."
        good = c.post("/api/plans/approve", json={"plan": PLAN, "by": "Ann"}).json()["plan"]
        run = c.post("/api/runs", json={"source_id": sid, "plan": good}).json()["run_id"]
        st = wait(c, run)
        assert st["status"] == "done" and st["report"]["approval"]["by"] == "Ann"
        assert st["report"]["approval"]["plan_hash"] == good["approval"]["plan_hash"]
        saved = json.loads(c.get(f"/api/runs/{run}/files/plan.json").content)
        assert saved["approval"]["by"] == "Ann"


def test_runs_409_checked_before_source_is_opened():
    with make_client() as c:
        sid = upload(c).json()["source_id"]
        # unapproved plan that would also fail in-place on CSV: 409 wins over 400
        r = c.post("/api/runs", json={"source_id": sid, "plan": PLAN, "in_place": True})
        assert r.status_code == 409


def test_llm_plan_endpoint_returns_no_approval():
    from scrubber.llm import FakeLLM

    with make_client(FakeLLM([{"columns": PLAN["columns"], "notes": []}])) as c:
        sid = upload(c, CSV).json()["source_id"]
        r = c.post("/api/plan", json={"source_id": sid, "rules": ["Trim"]})
        assert r.json()["plan"]["approval"] is None


# ------------------------------------------------------------------ CLI


def test_plan_command_writes_draft_and_hints(tmp_path, csv_file, monkeypatch):
    planner_llm(monkeypatch)
    rules = tmp_path / "rules.txt"
    rules.write_text("Trim names\n")
    out = tmp_path / "plan.json"
    r = invoke("plan", csv_file, "--rules", rules, "-o", out)
    assert r.exit_code == 0, r.output
    assert json.loads(out.read_text())["approval"] is None
    assert "scrubber approve" in r.stdout


def test_approve_with_by_yes(tmp_path):
    p = write_plan(tmp_path, SMALL)
    r = invoke("approve", p, "--by", "Ann", "--yes")
    assert r.exit_code == 0, r.output
    assert "approved by Ann" in r.stdout and "trim" in r.stdout
    assert Plan.model_validate_json(p.read_text()).is_approved()
    first = json.loads(p.read_text())["approval"]
    # re-approving an approved, unchanged plan replaces the approval
    assert invoke("approve", p, "--by", "Bo", "--yes").exit_code == 0
    second = json.loads(p.read_text())["approval"]
    assert second["by"] == "Bo" and second["plan_hash"] == first["plan_hash"]


def test_approve_prompts_and_declines(tmp_path):
    p = write_plan(tmp_path, SMALL)
    r = invoke("approve", p, input="Ann\nn\n")
    assert r.exit_code == 1 and "Approve this plan as Ann?" in r.stdout
    assert json.loads(p.read_text()).get("approval") is None
    r = invoke("approve", p, input="Ann\ny\n")
    assert r.exit_code == 0 and json.loads(p.read_text())["approval"]["by"] == "Ann"


def test_approve_blank_name_and_invalid_plan(tmp_path):
    p = write_plan(tmp_path, SMALL)
    assert invoke("approve", p, "--by", "  ", "--yes").exit_code == 2
    bad = write_plan(tmp_path, {"columns": [{"column": "n", "ops": [{"op": "nope"}]}]}, "bad.json")
    assert invoke("approve", bad, "--by", "Ann", "--yes").exit_code == 1


def test_approve_with_source_shows_preview(tmp_path, csv_file):
    p = write_plan(tmp_path, SMALL)
    r = invoke("approve", p, csv_file, "--by", "Ann", "--yes")
    assert r.exit_code == 0, r.output
    assert "Preview" in r.stdout and json.loads(p.read_text())["approval"]["by"] == "Ann"
    bad = write_plan(tmp_path, {"columns": [{"column": "zzz", "ops": [TRIM]}]}, "bad.json")
    r = invoke("approve", bad, csv_file, "--by", "Ann", "--yes")
    assert r.exit_code == 1 and "zzz" in r.output
    assert json.loads(bad.read_text()).get("approval") is None


def test_apply_refuses_unapproved_and_stale(tmp_path, csv_file):
    p = write_plan(tmp_path, SMALL)
    out = tmp_path / "o"
    r = invoke("apply", csv_file, "--plan", p, "--out", out)
    assert r.exit_code == 2 and "not approved" in r.output and "scrubber approve" in r.output
    assert not out.exists()
    stale = approved(SMALL, "Ann")
    stale["columns"][0]["ops"][0]["enabled"] = False
    p2 = write_plan(tmp_path, stale, "stale.json")
    r = invoke("apply", csv_file, "--plan", p2, "--out", out)
    assert r.exit_code == 2 and "approved by Ann" in r.output and not out.exists()


def test_apply_succeeds_after_approve(tmp_path, csv_file):
    p = write_plan(tmp_path, SMALL)
    assert invoke("approve", p, "--by", "Ann", "--yes").exit_code == 0
    out = tmp_path / "o"
    r = invoke("apply", csv_file, "--plan", p, "--out", out)
    assert r.exit_code == 0, r.output
    (run_dir,) = list(out.iterdir())
    assert json.loads((run_dir / "report.json").read_text())["approval"]["by"] == "Ann"


def test_run_yes_without_approved_by_fails(tmp_path, csv_file, monkeypatch):
    calls = planner_llm(monkeypatch)[1]
    rules = tmp_path / "rules.txt"
    rules.write_text("Trim names\n")
    out = tmp_path / "out"
    r = invoke("run", csv_file, "--rules", rules, "--yes", "--out", out)
    assert r.exit_code == 2 and "--approved-by" in r.output
    assert not out.exists() and calls == []  # failed before planning


def test_run_yes_with_approved_by_and_save_plan(tmp_path, csv_file, monkeypatch):
    planner_llm(monkeypatch)
    rules = tmp_path / "rules.txt"
    rules.write_text("Trim names\n")
    out, saved = tmp_path / "out", tmp_path / "approved.json"
    r = invoke("run", csv_file, "--rules", rules, "--yes", "--approved-by", "Bo", "--out", out,
               "--save-plan", saved)
    assert r.exit_code == 0, r.output
    assert Plan.model_validate_json(saved.read_text()).is_approved()
    (run_dir,) = list(out.iterdir())
    assert json.loads((run_dir / "report.json").read_text())["approval"]["by"] == "Bo"
