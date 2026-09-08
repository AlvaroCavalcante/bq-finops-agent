# bq-finops-agent

One-shot BigQuery SQL optimizer built with the [Agent Development Kit (ADK)](https://adk.dev/). Send a SQL query (and optional context); receive an optimized query, byte/cost comparison, and recommendations.

## Features

- **Pipeline architecture**: input (SQL + context) → metadata (discover tables + enrich + original-query bytes) → optimize (anti-patterns + rewrite) / validate loop → structured report
- **Cost measurement**: dry-run by default (reuses discovery cache for the same SQL); live only when tables are clustered or dry-run cannot estimate bytes
- **Table discovery**: BigQuery dry-run via `list_referenced_tables` — not LLM inference of FROM clauses
- **Safety**: blocks DML/DDL; `maximum_bytes_billed` cap on live runs
- **Deterministic checks**: SQL anti-patterns, output schema validation, readonly guardrails

## Quick start

> **Windows note:** if the project path contains non-ASCII characters (e.g. `Área de Trabalho`), create the venv on an ASCII path: `set UV_PROJECT_ENVIRONMENT=C:\venvs\bq-finops-agent` before `uv sync`.

```bash
cd bq-finops-agent
uv sync
cp .env.example .env   # set GOOGLE_CLOUD_PROJECT and credentials
agents-cli playground
```

Or run a single optimization:

```bash
agents-cli run "SELECT * FROM \`bigquery-public-data.samples.shakespeare\` LIMIT 5"
```

## Configuration

| Variable | Description | Default |
|----------|-------------|---------|
| `GOOGLE_CLOUD_PROJECT` | GCP project for BigQuery | required |
| `GOOGLE_GENAI_USE_VERTEXAI` | Use Vertex AI (not AI Studio) | `true` |
| `GOOGLE_CLOUD_LOCATION` | Gemini/Vertex region (`global`; do not use the BQ region) | `global` |
| `BQ_LOCATION` | BigQuery job location | `us-east4` |
| `BQ_MAX_BYTES_BILLED` | Cap for live query runs (bytes) | 50 GiB |
| `BQ_ON_DEMAND_USD_PER_TIB` | On-demand price for USD estimates | 6.25 |
| `FINOPS_DEBUG` | Stream full tool/agent traces in the playground | `false` |

## Architecture

```
START → input_agent → seed_pipeline_context → metadata_agent → optimizer_agent
      → validator_agent → decide_optimization_loop → render_report
```

`app/agent.py` holds the workflow. `input_agent` emits `PipelineInput`; seed copies `sql`/`context` to session state. Each later agent has an `output_schema` + `output_key`. Tools take explicit arguments (the agent copies `{original_sql?}` into `sql`) and return dicts. `after_tool_callback` projects those returns into session state so later prompts and tools can read them without the model rebuilding JSON.

### Tools

- `list_referenced_tables(sql)` — dry-run discovery of `referenced_tables`
- `measure_query_cost(sql, purpose)` — baseline or optimized; live vs dry-run comes from clustering / missing dry-run bytes, not from purpose
- `get_table_metadata`, `get_partition_summary`, `get_view_definition` — table/view facts (persisted by callback)
- `detect_sql_antipatterns(sql)` — optimizer tool; reads `tables_metadata` from state
- `validate_output_schema(original_schema, optimized_schema)` — schema check (persisted by callback)
- `compare_query_stats(baseline_bytes, optimized_bytes, …)` — byte comparison (persisted by callback)
- `diff_sql(before, after)` — unified diff of original vs rewritten SQL

## Output

`render_report` is a deterministic node (no model call): it builds the report from
session state, emits a Markdown report for the chat UI, and the structured
`OptimizationReport` JSON as the workflow output:

- `optimized_sql`, `baseline_bytes`, `optimized_bytes`, `savings_pct`
- `estimated_usd_*`, `measurement_method`, `changes_applied`
- `recommendations_not_applied`, `warnings`
- `baseline_measured`, `optimized_verified_live` (informational: true only when live/mixed was required)

## Evaluation

```bash
agents-cli eval run --dataset tests/eval/datasets/finops-dataset.json
```

Unit tests (deterministic tools):

```bash
uv run pytest tests/unit tests/integration -q
```

## Deploy (Agent Runtime)

Requires `agents-cli` (`uv tool install google-agents-cli`). The local `.env` is not copied into the container; pass the same values at deploy time:

```bash
agents-cli deploy \
  --project gcd-pr-dados \
  --region us-east1 \
  --update-env-vars "GOOGLE_GENAI_USE_VERTEXAI=true,GOOGLE_CLOUD_LOCATION=global,BQ_LOCATION=us-east4" \
  --no-confirm-project
```

`us-east1` is the Agent Runtime region. `GOOGLE_CLOUD_LOCATION=global` is for Gemini. `BQ_LOCATION=us-east4` is for BigQuery jobs.

After deploy:

```bash
agents-cli run --url URL_DO_ENGINE --mode adk "SELECT * FROM \`bigquery-public-data.samples.shakespeare\` LIMIT 5"
```

## Usage by engineering

The same agent can be invoked headlessly (API, CLI, A2A) with SQL extracted from your own `INFORMATION_SCHEMA.JOBS` or audit-log pipelines — mining logic stays outside this repo.

## License

Apache 2.0
