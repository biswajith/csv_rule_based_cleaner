"""Golden test: sample leads CSV + hand-written plan (no LLM) -> expected output file.

tests/data/leads_expected.csv was generated once and checked by eye (keypad letters,
11-digit '1' prefix, doubled '@', state corrected from city, 'New Jersey' -> 'NJ';
7/9/11-digit phones and malformed emails remain unchanged and are flagged)."""

from __future__ import annotations

import csv
from pathlib import Path

from util import read_csv

from scrubber.cleaner import clean
from scrubber.connectors import CSVSource, open_source
from scrubber.plan import Plan

DATA = Path(__file__).parent / "data"


def test_leads_golden(tmp_path):
    plan = Plan.model_validate_json((DATA / "leads_plan.json").read_text())
    rep = clean(open_source(CSVSource(path=str(DATA / "leads.csv"))), plan, None, run_dir=tmp_path / "run")
    assert rep.status == "done" and rep.rows == rep.rows_written == 50 and rep.llm.calls == 0
    out = tmp_path / "run" / "leads.cleaned.csv"
    assert out.read_text() == (DATA / "leads_expected.csv").read_text()
    # spot checks that document the intent
    rows = {r["first_name"]: r for r in csv.DictReader(out.open(newline=""))}
    assert rows["Sarah"]["phone"] == "(123) 456-7890" and rows["Sarah"]["state"] == "TX"
    assert rows["Christopher"]["phone"] == "(555) 123-2223"
    assert rows["Emily"]["email"] == "emily.davis@email.com"
    assert rows["Gregory"]["state"] == "NJ"
    flagged = {(r[1], r[3]) for r in read_csv(tmp_path / "run" / "flags.csv")[1:]}
    assert ("phone", "Not a 10-digit US number") in flagged and ("email", "Invalid email") in flagged
    assert rep.cells_changed == 66
