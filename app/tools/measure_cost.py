from __future__ import annotations

from typing import Literal

from google.adk.tools import ToolContext
from google.cloud import bigquery

from app.schemas import QueryMeasurement, dump_model
from app.tools.bq_client import get_bq_client, get_max_bytes_billed
from app.tools.safety import (
    LIVE_FORBIDDEN_MESSAGE,
    classify_sql_guard,
    dedupe_repeated_statement,
    forbids_live_execution,
)

Purpose = Literal["baseline", "optimized"]


def _schema_fields(job: bigquery.QueryJob) -> list[dict]:
    if job.schema is None:
        return []
    return [{"name": field.name, "type": field.field_type, "mode": field.mode} for field in job.schema]


def _referenced_tables(job: bigquery.QueryJob) -> list[dict]:
    tables = []
    if job.referenced_tables:
        for table in job.referenced_tables:
            tables.append(
                {
                    "project": table.project,
                    "dataset": table.dataset_id,
                    "table": table.table_id,
                }
            )
    return tables


def _run_query(sql: str, dry_run: bool) -> bigquery.QueryJob:
    client = get_bq_client()
    job_config = bigquery.QueryJobConfig(dry_run=dry_run)
    if not dry_run:
        job_config.maximum_bytes_billed = get_max_bytes_billed()
    return client.query(sql, job_config=job_config)


def _job_result(job: bigquery.QueryJob, method: str) -> dict:
    return {
        "status": "success",
        "total_bytes_processed": job.total_bytes_processed,
        "measurement_method": method,
        "referenced_tables": _referenced_tables(job),
        "output_schema": _schema_fields(job),
        "statement_type": job.statement_type,
        "job_id": job.job_id,
    }


def _as_measurement(data: dict) -> dict:
    return dump_model(QueryMeasurement, data)


def _execute(sql: str, dry_run: bool) -> dict:
    sql = dedupe_repeated_statement(sql)
    classified = classify_sql_guard(sql)
    if classified is not None:
        return _as_measurement(
            {"status": "error", "error_message": classified[1]}
        )
    if not dry_run and forbids_live_execution(sql):
        return _as_measurement(
            {"status": "error", "error_message": LIVE_FORBIDDEN_MESSAGE}
        )
    method = "dry_run" if dry_run else "live"
    try:
        job = _run_query(sql, dry_run=dry_run)
        return _as_measurement(_job_result(job, method))
    except Exception as exc:  # noqa: BLE001
        return _as_measurement({"status": "error", "error_message": str(exc)})


def _table_ref(table: dict) -> str:
    return f"{table.get('project')}.{table.get('dataset')}.{table.get('table')}"


def _baseline_blocked_without_metadata(tool_context: ToolContext) -> dict | None:
    """Require table metadata before baseline when the SQL references tables."""
    referenced = tool_context.state.get("referenced_tables") or []
    if not referenced:
        return None
    tables_meta = tool_context.state.get("tables_metadata") or {}
    missing = [
        ref
        for table in referenced
        if (ref := _table_ref(table)) not in tables_meta
    ]
    if not missing:
        return None
    return _as_measurement(
        {
            "status": "error",
            "error_message": (
                "Collect table metadata with get_table_metadata before measuring "
                "baseline. Missing: " + ", ".join(missing)
            ),
        }
    )


def _cached_dry_run(sql: str, tool_context: ToolContext) -> dict | None:
    cached = tool_context.state.get("discovery_dry_run")
    cached_sql = tool_context.state.get("discovery_sql")
    if (
        isinstance(cached, dict)
        and cached.get("status") == "success"
        and cached_sql == sql
    ):
        return dict(cached)
    return None


def list_referenced_tables(sql: str) -> dict:
    """Discover tables referenced by a SQL query via BigQuery dry-run.

    Never executes the query. On failure, does not invent table names.

    Args:
        sql: BigQuery SQL to inspect. Pass original_sql from the pipeline context.

    Returns:
        dict with referenced_tables on success, or status=error.
    """
    if not sql:
        return _as_measurement(
            {"status": "error", "error_message": "No SQL provided."}
        )
    return _execute(sql, dry_run=True)


def measure_query_cost(
    sql: str,
    purpose: Purpose,
    tool_context: ToolContext,
) -> dict:
    """Measure BigQuery bytes processed for a SQL query.

    Chooses live vs dry-run from session clustering state. Does not look
    up table metadata. Reuses a discovery dry-run cache when measuring
    the same SQL on the dry-run path. Falls back to live only when
    clustering is present or dry-run cannot estimate bytes. INSERT and
    MERGE are always dry-run; live execution is never used for them.

    For purpose=baseline, refuses to run until get_table_metadata has
    recorded every referenced table (so clustering is known). Queries
    with no referenced tables are measured immediately.

    Args:
        sql: BigQuery SQL to measure. Pass original_sql for baseline, or
            optimizer_step.optimized_sql for purpose=optimized.
        purpose: One of baseline or optimized.

    Returns:
        dict with bytes, measurement_method, schema, and referenced tables.
    """
    if not sql:
        return _as_measurement(
            {"status": "error", "error_message": "No SQL provided."}
        )

    if purpose == "baseline":
        blocked = _baseline_blocked_without_metadata(tool_context)
        if blocked is not None:
            return blocked

    has_clustering = bool(tool_context.state.get("has_clustering"))
    mutating = forbids_live_execution(sql)
    use_live = has_clustering and not mutating

    if use_live:
        final = _execute(sql, dry_run=False)
        if final.get("status") == "success":
            final["measurement_method"] = "live"
        return final

    dry_result = _cached_dry_run(sql, tool_context)
    if dry_result is None:
        dry_result = _execute(sql, dry_run=True)

    if dry_result.get("status") != "success":
        return dry_result

    if dry_result.get("total_bytes_processed") is None:
        if mutating:
            dry_result["measurement_method"] = "dry_run"
            return _as_measurement(dry_result)
        live_result = _execute(sql, dry_run=False)
        if live_result.get("status") != "success":
            return _as_measurement(
                {
                    "status": "error",
                    "error_message": live_result.get(
                        "error_message", "Live fallback failed."
                    ),
                }
            )
        final = dict(live_result)
        final["measurement_method"] = "mixed"
        final["dry_run_bytes"] = None
        return _as_measurement(final)

    dry_result["measurement_method"] = "dry_run"
    return _as_measurement(dry_result)
