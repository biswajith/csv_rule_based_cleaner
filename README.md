# Scrubber

Plan first, then clean. Scrubber cleans tabular data (CSV, SQL tables, MongoDB
collections) from plain-English rules:

1. **Profile** — code samples the data and summarises every column (types, value
   shapes like `(999) 999-9999`, top values, outliers).
2. **Plan** — one LLM call turns your rules + the profile into a JSON *plan*: per column,
   an ordered list of deterministic ops (regex, lookup tables, case, trim, date parsing,
   validation). The LLM delegates the work to code; it does not clean rows itself.
3. **Review & approve** — a dry run on the sample shows what each op would change or flag.
   Edit, disable or delete ops; save the plan and reuse it on new data at zero LLM cost.
   A named person must approve the plan before anything runs; editing it afterwards voids
   the approval.
4. **Clean** — code applies the plan to every record. Only cells the plan explicitly
   marks for judgement (`llm_fix`) go back to the LLM — collected across the whole
   dataset, de-duplicated and sent in batches, with a hard budget.

Records are never deleted; problems are flagged. The change log is computed by diffing
before/after values, so it always matches the data. Output goes to a new file / table /
collection unless you ask for in-place.

The full design is in [docs/SPEC.md](docs/SPEC.md).

## Install

Python ≥ 3.11 and Node ≥ 20.

```bash
uv venv .venv && uv pip install --python .venv -e "backend[dev]"
```

```bash
cd web && npm install && npm run build
```

```bash
cp .env.example .env   # then set OPENAI_API_KEY
```

The LLM is reached through LangChain (`LLM_MODEL=openai:gpt-5-mini` by default;
the single planning call uses `PLANNER_MODEL=openai:gpt-6-luna`). Only OpenAI is
installed today; another provider is a config change plus its `langchain-*` package.

## Web UI

```bash
.venv/bin/scrubber serve
```

Open http://127.0.0.1:8000 → Connect (upload a CSV, or a SQL URL / MongoDB URI) →
write rules → review and approve the plan → run → inspect changes and flags, download results.
The server binds to localhost only and has no authentication.

## CLI

```bash
.venv/bin/scrubber profile examples/leads.csv
```

```bash
.venv/bin/scrubber plan examples/leads.csv --rules examples/rules.txt -o plan.json
```

`plan` writes a draft. Review it (edit the JSON if you like), then record your approval in
the file; the preview on a source is optional but recommended:

```bash
.venv/bin/scrubber approve plan.json examples/leads.csv
```

```bash
.venv/bin/scrubber apply examples/leads.csv --plan plan.json
```

`apply` refuses a plan that is not approved, or that changed after approval. `run` does
plan → preview → approve → apply in one go (it asks for your name and confirmation;
`--yes` needs `--approved-by NAME`, and `--save-plan plan.json` keeps the approved plan). Sources:

| Source | Example |
|---|---|
| CSV | `data.csv` (`--delimiter`, `--encoding`) |
| PostgreSQL | `postgresql://user@host/db  --table leads` (`--schema crm`) |
| MySQL / MariaDB | `mysql://user@host/db  --table leads` |
| Other SQL | any SQLAlchemy URL whose driver is installed (SQLite works out of the box) |
| MongoDB | `mongodb://…  --db crm --collection leads` (`--filter '{"active": true}'`) |

In the web UI the SQL tab is a connection form (database type, host, port, database, user,
password); you can switch to pasting a URL. SQL tables need a single-column unique key (the primary key, or `--key-column`). The
PostgreSQL (psycopg) and MySQL (PyMySQL) drivers are installed with the package; MySQL
connections use `utf8mb4`.

Pass `env:NAME` instead of a URL to read it from an environment variable (keeps
passwords out of shell history). Useful run options: `--no-llm` (never call the LLM;
`llm_fix` cells are flagged), `--max-llm-cells N`, `--in-place` (SQL/Mongo, asks for
confirmation), `--overwrite`.

Each run writes `output/<run_id>/`: the cleaned CSV (for CSV sources), `changes.csv`
(the undo record), `flags.csv`, `plan.json` and `report.json` (counts, LLM calls and
tokens).

## What is sent to the model provider

- Planning: your rule text and sampled values from the column profile.
- `llm_fix`: only the triggered cell values and the context columns the plan names.

Nothing else leaves the machine. Loading a saved plan or running with `--no-llm` sends
nothing.

## Development

```bash
cd backend && ../.venv/bin/python -m pytest -q
```

```bash
cd web && npm test && npm run build
```

The MySQL / PostgreSQL tests run against real servers when these point at an empty scratch
database (otherwise they are skipped):

```bash
SCRUBBER_TEST_MYSQL_URL=mysql://root@127.0.0.1:3306/scrub_test SCRUBBER_TEST_POSTGRES_URL=postgresql://postgres@127.0.0.1:5432/scrub_test ../.venv/bin/python -m pytest -q tests/test_sql_dialects.py
```

`npm run dev` (port 5173) proxies `/api` to a `scrubber serve` on port 8000; run the
server with `SCRUBBER_DEV=1` so it accepts the Vite origin.
