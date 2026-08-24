from __future__ import annotations

from typing import Literal

from google.adk.tools import ToolContext
from google.cloud import bigquery

from app.tools.bq_client import get_bq_client, get_max_bytes_billed
from app.tools.safety import is_readonly_select

Purpose = Literal["baseline", "optimized_probe", "optimized_final"]


def _schema_fields(job: bigquery.QueryJob) -> list[dict]:
    if job.schema is None:
        return []
    return [{"name": f.name, "type": f.field_type, "mode": f.mode} for f in job.schema]


def _referenced_tables(job: bigquery.QueryJob) -> list[dict]:
    refs = []
    if job.referenced_tables:
        for t in job.referenced_tables:
            refs.append(
                {
                    "project": t.project,
                    "dataset": t.dataset_id,
                    "table": t.table_id,
                }
            )
    return refs


def _run_query(sql: str, dry_run: bool) -> bigquery.QueryJob:
    client = get_bq_client()
    job_config = bigquery.QueryJobConfig(dry_run=dry_run)
    if not dry_run:
        job_config.maximum_bytes_billed = get_max_bytes_billed()
    return client.query(sql, job_config=job_config)


def _job_result(job: bigquery.QueryJob, method: str) -> dict:
    bytes_processed = job.total_bytes_processed or 0
    return {
        "status": "success",
        "total_bytes_processed": bytes_processed,
        "measurement_method": method,
        "referenced_tables": _referenced_tables(job),
        "output_schema": _schema_fields(job),
        "statement_type": job.statement_type,
        "job_id": job.job_id,
    }


def _dry_run_unreliable(result: dict, has_clustering: bool) -> bool:
    if result.get("status") != "success":
        return True
    bytes_val = result.get("total_bytes_processed")
    if bytes_val is None:
        return True
    if has_clustering:
        return True
    return False


def _table_has_clustering(referenced: list[dict]) -> bool:
    from app.tools.metadata import get_table_metadata

    for ref in referenced:
        meta = get_table_metadata(ref["project"], ref["dataset"], ref["table"])
        if meta.get("status") == "success" and meta.get("clustering_fields"):
            return True
    return False


def measure_query_cost(
    sql: str,
    purpose: str,
    tool_context: ToolContext,
) -> dict:
    """Measure BigQuery bytes processed for a SQL query.

    Applies dry-run first; automatically falls back to live execution when
    dry-run is unreliable (clustering, missing estimate) or when purpose is
    optimized_final (always verifies with a live run).

    Args:
        sql: BigQuery SQL to measure.
        purpose: One of baseline, optimized_probe, or optimized_final.

    Returns:
        dict with bytes, measurement_method, schema, and referenced tables.
    """
    ok, err = is_readonly_select(sql)
    if not ok:
        return {"status": "error", "error_message": err}

    methods_used: list[str] = []
    dry_result: dict | None = None
    live_result: dict | None = None

    try:
        dry_job = _run_query(sql, dry_run=True)
        dry_result = _job_result(dry_job, "dry_run")
        methods_used.append("dry_run")
    except Exception as exc:  # noqa: BLE001
        dry_result = {"status": "error", "error_message": str(exc)}

    has_clustering = False
    if dry_result and dry_result.get("referenced_tables"):
        has_clustering = _table_has_clustering(dry_result["referenced_tables"])

    need_live = purpose == "optimized_final" or _dry_run_unreliable(
        dry_result or {}, has_clustering
    )

    if need_live:
        try:
            live_job = _run_query(sql, dry_run=False)
            live_result = _job_result(live_job, "live")
            methods_used.append("live")
        except Exception as exc:  # noqa: BLE001
            if dry_result and dry_result.get("status") == "success":
                live_result = None
                return {
                    **dry_result,
                    "measurement_method": "dry_run",
                    "warnings": [f"Live run failed: {exc}"],
                }
            return {"status": "error", "error_message": str(exc)}

    if live_result:
        final = live_result
        if "dry_run" in methods_used and "live" in methods_used:
            final["measurement_method"] = "mixed"
        else:
            final["measurement_method"] = "live"
        final["dry_run_bytes"] = (
            dry_result.get("total_bytes_processed") if dry_result else None
        )
    else:
        final = dry_result or {"status": "error", "error_message": "Measurement failed."}
        final["measurement_method"] = "dry_run"

    if purpose == "baseline":
        tool_context.state["baseline"] = final
        tool_context.state["baseline_measured"] = final.get("status") == "success"

    if purpose in ("optimized_probe", "optimized_final"):
        tool_context.state["optimized_measurement"] = final
        if "live" in methods_used:
            tool_context.state["optimized_verified_live"] = True

    return final
