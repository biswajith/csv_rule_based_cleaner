# Scrubber

Plan first, then clean. Scrubber cleans tabular data (CSV, SQL tables, MongoDB
collections) from plain-English rules:

1. **Profile** — code samples the data and summarises every column (types, value
   shapes like `(999) 999-9999`, top values, outliers).
2. **Plan** — one LLM call turns your rules + the profile into a JSON *plan*: per column,
   an ordered list of deterministic ops (regex, lookup tables, case, trim, date parsing,
   validation). The LLM delegates the work to code; it does not clean rows itself.
3. **Review** — a dry run on the sample shows what each op would change or flag. Edit,
   disable or delete ops; save the plan and reuse it on new data at zero LLM cost.
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
`PLANNER_MODEL` can point the single planning call at a stronger model). Only OpenAI is
installed today; another provider is a config change plus its `langchain-*` package.

## Web UI

```bash
.venv/bin/scrubber serve
```

Open http://127.0.0.1:8000 → Connect (upload a CSV, or a SQL URL / MongoDB URI) →
write rules → review the plan → run → inspect changes and flags, download results.
The server binds to localhost only and has no authentication.

## CLI

```bash
.venv/bin/scrubber profile examples/leads.csv
```

```bash
.venv/bin/scrubber plan examples/leads.csv --rules examples/rules.txt -o plan.json
```

```bash
.venv/bin/scrubber apply examples/leads.csv --plan plan.json
```

`run` does plan → preview → confirm → apply in one go. Sources:

| Source | Example |
|---|---|
| CSV | `data.csv` (`--delimiter`, `--encoding`) |
| SQL (any SQLAlchemy URL) | `postgresql://…  --table leads` (needs a single-column unique key; `--key-column`) |
| MongoDB | `mongodb://…  --db crm --collection leads` (`--filter '{"active": true}'`) |

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

`npm run dev` (port 5173) proxies `/api` to a `scrubber serve` on port 8000; run the
server with `SCRUBBER_DEV=1` so it accepts the Vite origin.
