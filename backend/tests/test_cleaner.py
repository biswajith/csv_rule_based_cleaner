from __future__ import annotations

import json
import threading

import pytest
from util import mkplan, read_csv, write_csv

from scrubber.cleaner import clean, new_run_id
from scrubber.config import get_settings
from scrubber.connectors import CSVSource, OutputExistsError, SourceError, open_source
from scrubber.llm import FakeLLM, LLMError
from scrubber.planner import PlanError

HEADER = ["id", "name", "email"]
VAL = {"op": "validate", "pattern": r"[a-z.]+@[a-z]+\.com", "message": "Invalid email"}
FIX = {"op": "llm_fix", "instruction": "repair the email", "context_columns": ["name"]}
PLAN = mkplan(("email", [{"op": "trim"}, {"op": "case", "mode": "lower"}, VAL, FIX]))
PLAN_NO_LLM = mkplan(("email", [{"op": "trim"}, {"op": "case", "mode": "lower"}, VAL]))


def make_conn(tmp_path, rows, name="in.csv"):
    return open_source(CSVSource(path=str(write_csv(tmp_path / name, [HEADER] + rows))))


def fixer(mapping=None, **extra):
    """FakeLLM responder: looks values up in `mapping`; otherwise fixed=false."""
    mapping = mapping or {}

    def respond(messages, schema):
        items = json.loads(messages[-1][1].split("Items (JSON data):\n")[1])
        return {"results": [
            {"id": it["id"], "value": mapping.get(it["value"]), "fixed": it["value"] in mapping,
             "reason": "" if it["value"] in mapping else "unsure"} for it in items]}

    return FakeLLM(respond)


def items_sent(llm):
    out = []
    for messages, _ in llm.calls:
        out += json.loads(messages[-1][1].split("Items (JSON data):\n")[1])
    return out


def flags(run_dir):
    return read_csv(run_dir / "flags.csv")[1:]


def changes(run_dir):
    return read_csv(run_dir / "changes.csv")[1:]


def test_new_run_id_format():
    import re
    assert re.fullmatch(r"\d{8}-\d{6}-[0-9a-f]{4}", new_run_id())


def test_end_to_end_changes_equal_actual_diff(tmp_path):
    rows = [["1", "Ann", " ANN@X.COM "], ["2", "Bob", "bob@x.com"], ["3", "Cy", "cy-at-x"], ["4", "Di", ""]]
    c = make_conn(tmp_path, rows)
    llm = fixer({"cy-at-x": "cy@x.com"})
    run = tmp_path / "run1"
    rep = clean(c, PLAN, llm, run_dir=run)
    assert rep.status == "done" and rep.error is None
    out = read_csv(run / "in.cleaned.csv")
    assert out == [HEADER, ["1", "Ann", "ann@x.com"], ["2", "Bob", "bob@x.com"],
                   ["3", "Cy", "cy@x.com"], ["4", "Di", ""]]
    # changes.csv equals the real diff of input vs output
    inp = read_csv(tmp_path / "in.csv")
    diff = [[str(i - 1), HEADER[j], inp[i][j], out[i][j]] for i in range(1, len(inp))
            for j in range(len(HEADER)) if inp[i][j] != out[i][j]]
    assert [r[:4] for r in changes(run)] == diff
    assert changes(run)[0][4] == "trim;case" and changes(run)[1][4] == "llm_fix"
    assert flags(run) == []
    assert (rep.rows, rep.rows_written, rep.cells_changed, rep.cells_flagged) == (4, 4, 2, 0)
    assert rep.llm.model == "fake" and rep.llm.calls == 1 and rep.llm.cells_fixed == 1
    assert (rep.llm.cells_triggered, rep.llm.cells_unique, rep.llm.cells_sent) == (1, 1, 1)
    assert rep.columns[0].column == "email" and rep.columns[0].cells_changed == 2
    assert [o.op for o in rep.columns[0].ops] == ["trim", "case", "validate", "llm_fix"]
    saved = json.loads((run / "report.json").read_text())
    assert saved["run_id"] == "run1" and saved["status"] == "done" and saved["target"].endswith("in.cleaned.csv")
    for k in ("source", "in_place", "started_at", "finished_at", "duration_s", "llm", "columns"):
        assert k in saved
    assert json.loads((run / "plan.json").read_text())["columns"][0]["column"] == "email"
    assert (run / "changes.csv").read_text().splitlines()[0] == "row_key,column,before,after,sources"
    assert (run / "flags.csv").read_text().splitlines()[0] == "row_key,column,value,message"


def test_identical_bad_cells_sent_once(tmp_path):
    c = make_conn(tmp_path, [[str(i), "Ann", "bad"] for i in range(10)])
    llm = fixer({"bad": "ok@x.com"})
    rep = clean(c, PLAN, llm, run_dir=tmp_path / "r")
    assert len(items_sent(llm)) == 1
    assert rep.llm.cells_triggered == 10 and rep.llm.cells_unique == 1 and rep.llm.cells_fixed == 10
    assert rep.cells_changed == 10


def test_same_value_different_context_is_two_items(tmp_path):
    c = make_conn(tmp_path, [["1", "Ann", "bad"], ["2", "Bob", "bad"], ["3", "Ann", "bad"]])
    llm = fixer({"bad": "ok@x.com"})
    clean(c, PLAN, llm, run_dir=tmp_path / "r")
    sent = items_sent(llm)
    assert sorted(i["context"]["name"] for i in sent) == ["Ann", "Bob"]


def test_prompt_contains_instruction_must_match_and_ids(tmp_path):
    c = make_conn(tmp_path, [["1", "Ann", "bad"], ["2", "", "worse"]])
    llm = fixer()
    clean(c, PLAN, llm, run_dir=tmp_path / "r")
    system, human = llm.calls[0][0]
    assert system[0] == "system" and "must not blank" in system[1]
    assert "repair the email" in human[1] and VAL["pattern"] in human[1]
    items = items_sent(llm)
    assert [i["id"] for i in items] == ["0", "1"]
    assert "context" not in items[1]  # empty context values omitted


class CountingConn:
    """Delegates to a connector, counting iter_batches calls and optionally altering pass 2."""

    def __init__(self, inner, on_second=None, fail_on_batch=None):
        self.inner, self.passes, self.on_second, self.fail_on_batch = inner, 0, on_second, fail_on_batch

    def __getattr__(self, name):
        return getattr(self.inner, name)

    def iter_batches(self, size):
        self.passes += 1
        for n, batch in enumerate(self.inner.iter_batches(size)):
            if self.fail_on_batch is not None and n == self.fail_on_batch:
                raise SourceError("connection lost")
            if self.on_second and self.passes == 2:
                batch = self.on_second(batch)
            yield batch


def test_scan_pass_skipped_without_llm_fix_and_with_no_llm(tmp_path):
    c = CountingConn(make_conn(tmp_path, [["1", "A", "bad"]]))
    clean(c, PLAN_NO_LLM, None, run_dir=tmp_path / "r1")
    assert c.passes == 1
    c = CountingConn(make_conn(tmp_path, [["1", "A", "bad"]], "in2.csv"))
    rep = clean(c, PLAN, None, no_llm=True, run_dir=tmp_path / "r2")
    assert c.passes == 1 and rep.llm.calls == 0
    c = CountingConn(make_conn(tmp_path, [["1", "A", "bad"]], "in3.csv"))
    clean(c, PLAN, fixer(), run_dir=tmp_path / "r3")
    assert c.passes == 2


def test_no_llm_flags_skipped_and_needs_no_llm(tmp_path):
    c = make_conn(tmp_path, [["1", "A", "bad"], ["2", "B", "fine@x.com"]])
    rep = clean(c, PLAN, None, no_llm=True, run_dir=tmp_path / "r")
    assert rep.status == "done"
    assert flags(tmp_path / "r") == [["0", "email", "bad", "Invalid email"], ["0", "email", "bad", "LLM skipped"]]
    assert (rep.cells_flagged, rep.flags_written) == (1, 2)  # one cell, two flags


def test_llm_required_when_plan_has_llm_fix(tmp_path):
    c = make_conn(tmp_path, [["1", "A", "bad"]])
    with pytest.raises(LLMError):
        clean(c, PLAN, None, run_dir=tmp_path / "r")
    assert not (tmp_path / "r" / "in.cleaned.csv").exists()  # nothing was created


def test_plan_errors_raise_before_reading(tmp_path):
    c = CountingConn(make_conn(tmp_path, [["1", "A", "bad"]]))
    with pytest.raises(PlanError):
        clean(c, mkplan(("nope", [{"op": "trim"}])), None, run_dir=tmp_path / "r")
    assert c.passes == 0


def test_in_place_not_supported_for_csv_and_existing_output(tmp_path):
    c = make_conn(tmp_path, [["1", "A", "x"]])
    with pytest.raises(SourceError, match="in-place"):
        clean(c, PLAN_NO_LLM, None, in_place=True, run_dir=tmp_path / "r")
    clean(c, PLAN_NO_LLM, None, run_dir=tmp_path / "r2")
    with pytest.raises(OutputExistsError):
        clean(c, PLAN_NO_LLM, None, run_dir=tmp_path / "r2")
    assert clean(c, PLAN_NO_LLM, None, run_dir=tmp_path / "r2", overwrite=True).status == "done"


def test_max_llm_cells_cap_first_seen_sent_rest_flagged(tmp_path):
    names = "abcd"
    c = make_conn(tmp_path, [[str(i), "A", "bad" + names[i]] for i in range(4)])
    mapping = {"bad" + n: "ok%s@x.com" % n for n in names}
    llm = fixer(mapping)
    rep = clean(c, PLAN, llm, max_llm_cells=2, run_dir=tmp_path / "r")
    assert [i["value"] for i in items_sent(llm)] == ["bada", "badb"]
    out = read_csv(tmp_path / "r" / "in.cleaned.csv")
    assert [r[2] for r in out[1:]] == ["oka@x.com", "okb@x.com", "badc", "badd"]
    assert [f[3] for f in flags(tmp_path / "r") if f[3] != "Invalid email"] == ["LLM budget exceeded"] * 2
    assert (rep.llm.cells_over_budget, rep.llm.cells_unique, rep.llm.cells_sent) == (2, 4, 2)


def test_default_cap_comes_from_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("MAX_LLM_CELLS", "1")
    get_settings.cache_clear()
    c = make_conn(tmp_path, [["1", "A", "bad1"], ["2", "A", "bad2"]])
    llm = fixer()
    clean(c, PLAN, llm, run_dir=tmp_path / "r")
    assert len(items_sent(llm)) == 1


def test_chunking_by_cells_per_call(tmp_path, monkeypatch):
    monkeypatch.setenv("LLM_CELLS_PER_CALL", "2")
    get_settings.cache_clear()
    c = make_conn(tmp_path, [[str(i), "A", "bad%d" % i] for i in range(5)])
    llm = fixer()
    clean(c, PLAN, llm, run_dir=tmp_path / "r")
    assert len(llm.calls) == 3 and [len(json.loads(m[-1][1].split("Items (JSON data):\n")[1])) for m, _ in llm.calls] == [2, 2, 1]


def test_missing_unknown_and_duplicate_ids(tmp_path):
    c = make_conn(tmp_path, [["1", "A", "bad1"], ["2", "A", "bad2"], ["3", "A", "bad3"]])
    llm = FakeLLM([{"results": [
        {"id": "0", "value": "one@x.com", "fixed": True, "reason": ""},
        {"id": "0", "value": "dup@x.com", "fixed": True, "reason": ""},   # duplicate: first wins
        {"id": "99", "value": "ghost@x.com", "fixed": True, "reason": ""},  # unknown: ignored
        {"id": "2", "value": None, "fixed": False, "reason": "cannot tell"},
        # id "1" missing
    ]}])
    clean(c, PLAN, llm, run_dir=tmp_path / "r")
    out = read_csv(tmp_path / "r" / "in.cleaned.csv")
    assert [r[2] for r in out[1:]] == ["one@x.com", "bad2", "bad3"]
    msgs = [f[3] for f in flags(tmp_path / "r")]
    assert "LLM could not fix: no answer" in msgs and "LLM could not fix: cannot tell" in msgs


def test_rejected_answer_flagged_and_unchanged(tmp_path):
    c = make_conn(tmp_path, [["1", "A", "bad"]])
    rep = clean(c, PLAN, fixer({"bad": "still not an email"}), run_dir=tmp_path / "r")
    assert read_csv(tmp_path / "r" / "in.cleaned.csv")[1][2] == "bad"
    assert [f[3] for f in flags(tmp_path / "r")] == ["Invalid email", "LLM fix rejected: result failed validation"]
    assert rep.llm.cells_fixed == 0


def test_failing_calls_flag_three_strikes_stop_and_run_completes(tmp_path, monkeypatch):
    monkeypatch.setenv("LLM_CELLS_PER_CALL", "1")
    monkeypatch.setenv("LLM_CONCURRENCY", "1")
    get_settings.cache_clear()
    n = 6
    c = make_conn(tmp_path, [[str(i), "A", "bad%d" % i] for i in range(n)])
    llm = FakeLLM(lambda m, s: RuntimeError("503 overloaded"))
    rep = clean(c, PLAN, llm, run_dir=tmp_path / "r")
    assert rep.status == "done" and rep.rows_written == n and len(llm.calls) == 3
    msgs = [f[3] for f in flags(tmp_path / "r") if f[3] != "Invalid email"]
    assert msgs[:3] == ["LLM error: RuntimeError: 503 overloaded"] * 3 and msgs[3:] == ["LLM error: LLM unavailable"] * 3
    assert rep.llm.failed_calls == 3 and rep.llm.cells_sent == 3
    assert len(read_csv(tmp_path / "r" / "in.cleaned.csv")) == n + 1


def test_success_resets_consecutive_failures(tmp_path, monkeypatch):
    monkeypatch.setenv("LLM_CELLS_PER_CALL", "1")
    monkeypatch.setenv("LLM_CONCURRENCY", "1")
    get_settings.cache_clear()
    c = make_conn(tmp_path, [[str(i), "A", "bad%d" % i] for i in range(6)])
    ok = {"results": [{"id": "0", "value": "g@x.com", "fixed": True}]}
    llm = FakeLLM([RuntimeError("x"), RuntimeError("x"), ok, RuntimeError("x"), RuntimeError("x"), ok])
    rep = clean(c, PLAN, llm, run_dir=tmp_path / "r")
    assert len(llm.calls) == 6 and rep.llm.cells_fixed == 2


def test_source_changed_between_passes(tmp_path):
    c = CountingConn(make_conn(tmp_path, [["1", "A", "bad"]]),
                     on_second=lambda b: [(k, {**r, "email": "different"}) for k, r in b])
    rep = clean(c, PLAN, fixer({"bad": "ok@x.com"}), run_dir=tmp_path / "r")
    assert "LLM fix not attempted (source changed during run)" in [f[3] for f in flags(tmp_path / "r")]
    assert rep.status == "done"


def test_cancel_before_start_and_during_write(tmp_path, monkeypatch):
    c = make_conn(tmp_path, [[str(i), "A", "x@x.com"] for i in range(6)])
    ev = threading.Event()
    ev.set()
    rep = clean(c, PLAN_NO_LLM, None, run_dir=tmp_path / "r1", cancel=ev)
    assert rep.status == "cancelled" and rep.rows_written == 0
    assert json.loads((tmp_path / "r1" / "report.json").read_text())["status"] == "cancelled"

    monkeypatch.setenv("BATCH_SIZE", "2")
    get_settings.cache_clear()
    ev = threading.Event()
    seen = []

    def progress(phase, done, total):
        seen.append((phase, done, total))
        ev.set()

    rep = clean(c, PLAN_NO_LLM, None, run_dir=tmp_path / "r2", cancel=ev, progress=progress)
    assert rep.status == "cancelled" and rep.rows_written == 2
    assert len(read_csv(tmp_path / "r2" / "in.cleaned.csv")) == 3  # header + 2 rows


def test_cancel_during_resolve_skips_calls_and_write(tmp_path, monkeypatch):
    monkeypatch.setenv("LLM_CELLS_PER_CALL", "1")
    monkeypatch.setenv("LLM_CONCURRENCY", "1")
    get_settings.cache_clear()
    c = make_conn(tmp_path, [[str(i), "A", "bad%d" % i] for i in range(4)])
    ev = threading.Event()
    llm = FakeLLM(lambda m, s: (ev.set(), {"results": []})[1])
    rep = clean(c, PLAN, llm, run_dir=tmp_path / "r", cancel=ev)
    assert rep.status == "cancelled" and len(llm.calls) == 1 and rep.rows_written == 0


def test_connector_failure_midway_recorded_not_raised(tmp_path, monkeypatch):
    monkeypatch.setenv("BATCH_SIZE", "2")
    get_settings.cache_clear()
    c = CountingConn(make_conn(tmp_path, [[str(i), "A", " x@x.com"] for i in range(6)]), fail_on_batch=1)
    rep = clean(c, PLAN_NO_LLM, None, run_dir=tmp_path / "r")
    assert rep.status == "failed" and "connection lost" in rep.error and rep.rows_written == 2
    saved = json.loads((tmp_path / "r" / "report.json").read_text())
    assert saved["status"] == "failed" and saved["rows_written"] == 2
    assert len(changes(tmp_path / "r")) == 2  # undo record for what was written


def test_progress_phases(tmp_path):
    c = make_conn(tmp_path, [["1", "A", "bad"], ["2", "A", "bad2"]])
    seen = []
    clean(c, PLAN, fixer(), run_dir=tmp_path / "r", progress=lambda p, d, t: seen.append((p, d, t)))
    phases = [p for p, _, _ in seen]
    assert phases == ["scanning", "resolving", "resolving", "writing"]
    assert seen[0] == ("scanning", 2, 2) and seen[-1] == ("writing", 2, 2) and seen[2] == ("resolving", 1, 1)


def test_extra_fields_flagged_and_preserved(tmp_path):
    p = write_csv(tmp_path / "in.csv", [HEADER, ["1", "A", " x@x.com ", "extra1", "extra2"], ["2", "B", "y@x.com"]])
    c = open_source(CSVSource(path=str(p)))
    rep = clean(c, PLAN_NO_LLM, None, run_dir=tmp_path / "r")
    out = read_csv(tmp_path / "r" / "in.cleaned.csv")
    assert out[1] == ["1", "A", "x@x.com", "extra1", "extra2"]
    assert flags(tmp_path / "r") == [["0", "", "", "Row has 2 extra field(s)"]]
    assert rep.cells_flagged == 1 and rep.rows_written == 2


def test_csv_none_equals_empty_no_change(tmp_path):
    c = make_conn(tmp_path, [["1", "A", ""], ["2", "A", "N/A"]])
    plan = mkplan(("email", [{"op": "null_if", "values": ["n/a"]}]))
    rep = clean(c, plan, None, run_dir=tmp_path / "r")
    assert changes(tmp_path / "r") == [["1", "email", "N/A", "", "null_if"]]
    assert rep.cells_changed == 1


def test_rows_preserved_and_order_kept_across_batches(tmp_path, monkeypatch):
    monkeypatch.setenv("BATCH_SIZE", "3")
    get_settings.cache_clear()
    rows = [[str(i), "N%d" % i, " X%d@x.com" % i] for i in range(10)]
    rep = clean(make_conn(tmp_path, rows), PLAN_NO_LLM, None, run_dir=tmp_path / "r")
    out = read_csv(tmp_path / "r" / "in.cleaned.csv")
    assert [r[0] for r in out[1:]] == [str(i) for i in range(10)] and rep.rows == 10
