# bq-finops-agent

One-shot BigQuery SQL optimizer built with the [Agent Development Kit (ADK)](https://adk.dev/). Send a SQL query (and optional context); receive an optimized query, byte/cost comparison, and recommendations.

## Features

- **Pipeline architecture**: input (SQL + context) → metadata (discover tables + enrich + original-query bytes) → optimize/validate loop → structured report
- **Cost measurement**: live when tables are clustered or purpose is `optimized_final`; otherwise dry-run (reuses discovery cache for the same SQL)
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
| `BQ_LOCATION` | BigQuery job location | `US` |
| `BQ_MAX_BYTES_BILLED` | Cap for live query runs (bytes) | 50 GiB |
| `BQ_ON_DEMAND_USD_PER_TIB` | On-demand price for USD estimates | 6.25 |

## Architecture

```
START → input_agent → seed_pipeline_context → metadata_agent → optimizer_agent
      → validator_agent → decide_optimization_loop → formatter_agent
```

`app/agent.py` holds the workflow. `input_agent` emits `PipelineInput`; seed copies `sql`/`context` to session state. Each later agent has an `output_schema` + `output_key`. Tools take explicit arguments (the agent copies `{original_sql?}` into `sql`). Large maps (`tables_metadata`, `baseline`) are persisted in session state so later prompts can inject them without the model rebuilding JSON.

### Tools

- `list_referenced_tables(sql)` — dry-run discovery of `referenced_tables`
- `measure_query_cost(sql, purpose)` — baseline / optimized_probe / optimized_final
- `get_table_metadata`, `get_partition_summary`, `get_view_definition` — persist into session state
- `detect_sql_antipatterns(sql)` — reads `tables_metadata` from state
- `validate_output_schema(original_schema, optimized_schema)` — persist schema check into state
- `compare_query_stats(baseline_bytes, optimized_bytes, …)` — persist byte comparison into state
- `diff_sql(before, after)` — unified diff of original vs rewritten SQL

## Output

Structured `OptimizationReport` JSON:

- `optimized_sql`, `baseline_bytes`, `optimized_bytes`, `savings_pct`
- `estimated_usd_*`, `measurement_method`, `changes_applied`
- `recommendations_not_applied`, `warnings`
- `baseline_measured`, `optimized_verified_live`

## Evaluation

```bash
agents-cli eval run --dataset tests/eval/datasets/finops-dataset.json
```

Unit tests (deterministic tools):

```bash
uv run pytest tests/unit tests/integration -q
```

## Usage by engineering

The same agent can be invoked headlessly (API, CLI, A2A) with SQL extracted from your own `INFORMATION_SCHEMA.JOBS` or audit-log pipelines — mining logic stays outside this repo.

## License

Apache 2.0
