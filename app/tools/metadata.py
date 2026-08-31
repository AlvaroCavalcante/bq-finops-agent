from google.adk.tools import ToolContext
from google.cloud import bigquery
from google.cloud.exceptions import NotFound

from app.tools.bq_client import get_bq_client


def _table_ref_str(ref) -> str | None:
    if ref is None:
        return None
    project = getattr(ref, "project", None)
    dataset = getattr(ref, "dataset_id", None)
    table = getattr(ref, "table_id", None)
    if project and dataset and table:
        return f"{project}.{dataset}.{table}"
    return None


def _serialize_key_constraints(tbl) -> tuple[list[str], list[dict]]:
    """Extract unenforced PK/FK from a Table resource (no extra API call)."""
    constraints = getattr(tbl, "table_constraints", None)
    if constraints is None:
        return [], []

    primary_key_columns: list[str] = []
    pk = getattr(constraints, "primary_key", None)
    columns = getattr(pk, "columns", None) if pk is not None else None
    if isinstance(columns, (list, tuple)):
        primary_key_columns = [str(c) for c in columns]

    foreign_keys: list[dict] = []
    fks = getattr(constraints, "foreign_keys", None)
    if not isinstance(fks, (list, tuple)):
        return primary_key_columns, foreign_keys

    for fk in fks:
        column_references = []
        for cr in getattr(fk, "column_references", None) or []:
            referencing = getattr(cr, "referencing_column", None)
            referenced = getattr(cr, "referenced_column", None)
            if referencing and referenced:
                column_references.append(
                    {
                        "referencing_column": str(referencing),
                        "referenced_column": str(referenced),
                    }
                )
        foreign_keys.append(
            {
                "name": getattr(fk, "name", None),
                "referenced_table": _table_ref_str(
                    getattr(fk, "referenced_table", None)
                ),
                "column_references": column_references,
            }
        )
    return primary_key_columns, foreign_keys


def _record_table_metadata(result: dict, tool_context: ToolContext | None) -> None:
    if tool_context is None or result.get("status") != "success":
        return
    ref = f"{result['project']}.{result['dataset']}.{result['table']}"
    tables = dict(tool_context.state.get("tables_metadata") or {})
    tables[ref] = result
    tool_context.state["tables_metadata"] = tables
    tool_context.state["has_clustering"] = any(
        bool(meta.get("clustering_fields"))
        for meta in tables.values()
        if meta.get("status") == "success"
    )


def get_table_metadata(
    project: str,
    dataset: str,
    table: str,
    tool_context: ToolContext | None = None,
) -> dict:
    """Fetch BigQuery table metadata for optimization decisions.

    Args:
        project: GCP project id.
        dataset: BigQuery dataset id.
        table: BigQuery table id.

    Returns:
        dict with partitioning, clustering, primary_key_columns, foreign_keys,
        schema, and size stats.
    """
    client = get_bq_client()
    ref = f"{project}.{dataset}.{table}"
    try:
        tbl = client.get_table(ref)
    except NotFound:
        return {"status": "error", "error_message": f"Table not found: {ref}"}

    time_partitioning = None
    if tbl.time_partitioning:
        tp = tbl.time_partitioning
        time_partitioning = {
            "type": tp.type_,
            "field": tp.field,
            "require_partition_filter": tp.require_partition_filter,
            "expiration_ms": tp.expiration_ms,
        }

    range_partitioning = None
    if tbl.range_partitioning:
        rp = tbl.range_partitioning
        range_partitioning = {
            "field": rp.field,
            "range": {
                "start": rp.range_.start,
                "end": rp.range_.end,
                "interval": rp.range_.interval,
            },
        }

    schema = [
        {"name": field.name, "type": field.field_type, "mode": field.mode}
        for field in tbl.schema
    ]
    primary_key_columns, foreign_keys = _serialize_key_constraints(tbl)

    result = {
        "status": "success",
        "project": project,
        "dataset": dataset,
        "table": table,
        "table_type": tbl.table_type,
        "num_bytes": tbl.num_bytes,
        "num_rows": tbl.num_rows,
        "time_partitioning": time_partitioning,
        "range_partitioning": range_partitioning,
        "clustering_fields": list(tbl.clustering_fields or []),
        "primary_key_columns": primary_key_columns,
        "foreign_keys": foreign_keys,
        "require_partition_filter": (
            time_partitioning.get("require_partition_filter")
            if time_partitioning
            else False
        ),
        "schema": schema,
    }
    _record_table_metadata(result, tool_context)
    return result


def get_partition_summary(project: str, dataset: str, table: str) -> dict:
    """Summarize partition ranges for a partitioned table.

    Args:
        project: GCP project id.
        dataset: BigQuery dataset id.
        table: BigQuery table id.

    Returns:
        dict with partition count and min/max partition ids when available.
    """
    client = get_bq_client()

    query = f"""
        SELECT
          COUNT(*) AS partition_count,
          MIN(partition_id) AS min_partition_id,
          MAX(partition_id) AS max_partition_id,
          SUM(total_rows) AS total_rows,
          SUM(total_logical_bytes) AS total_logical_bytes
        FROM `{project}.{dataset}.INFORMATION_SCHEMA.PARTITIONS`
        WHERE table_name = @table_name
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("table_name", "STRING", table),
        ]
    )
    try:
        rows = list(client.query(query, job_config=job_config).result())
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "error_message": str(exc)}

    if not rows:
        return {"status": "success", "partition_count": 0}

    row = rows[0]
    return {
        "status": "success",
        "partition_count": row.partition_count,
        "min_partition_id": row.min_partition_id,
        "max_partition_id": row.max_partition_id,
        "total_rows": row.total_rows,
        "total_logical_bytes": row.total_logical_bytes,
    }


def get_view_definition(project: str, dataset: str, view: str) -> dict:
    """Return the SQL definition of a BigQuery view.

    Args:
        project: GCP project id.
        dataset: BigQuery dataset id.
        view: BigQuery view id.

    Returns:
        dict with view_query text when the resource is a view.
    """
    client = get_bq_client()
    ref = f"{project}.{dataset}.{view}"
    try:
        tbl = client.get_table(ref)
    except NotFound:
        return {"status": "error", "error_message": f"View not found: {ref}"}

    if tbl.table_type != "VIEW":
        return {
            "status": "error",
            "error_message": f"{ref} is not a view (type={tbl.table_type}).",
        }

    return {
        "status": "success",
        "project": project,
        "dataset": dataset,
        "view": view,
        "view_query": tbl.view_query,
    }
