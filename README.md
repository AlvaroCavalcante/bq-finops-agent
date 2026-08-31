# bq-finops-agent

One-shot BigQuery SQL optimizer built with the [Agent Development Kit (ADK)](https://adk.dev/). Send a SQL query (and optional context); receive an optimized query, byte/cost comparison, and recommendations.

## Features

- **Pipeline architecture**: metadata (discover tables + enrich + original-query bytes) → optimize/validate loop → structured report
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
metadata_agent → LoopAgent(optimizer, validator) → formatter_agent
```

### Tools

- `list_referenced_tables(sql)` — dry-run discovery of `referenced_tables`
- `measure_query_cost(sql, purpose)` — baseline / optimized_probe / optimized_final
- `get_table_metadata`, `get_partition_summary`, `get_view_definition`
- `detect_sql_antipatterns`, `validate_output_schema`, `compare_query_stats`, `diff_sql`

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
