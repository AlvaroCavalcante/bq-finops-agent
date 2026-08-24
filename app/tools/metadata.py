
from google.cloud import bigquery
from google.cloud.exceptions import NotFound

from app.tools.bq_client import get_bq_client


def get_table_metadata(project: str, dataset: str, table: str) -> dict:
    """Fetch BigQuery table metadata for optimization decisions.

    Args:
        project: GCP project id.
        dataset: BigQuery dataset id.
        table: BigQuery table id.

    Returns:
        dict with partitioning, clustering, schema, and size stats.
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
        {"name": f.name, "type": f.field_type, "mode": f.mode}
        for f in tbl.schema
    ]

    return {
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
        "require_partition_filter": (
            time_partitioning.get("require_partition_filter")
            if time_partitioning
            else False
        ),
        "schema": schema,
    }


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
    location = client.location or "US"
    region = location if location.startswith("region-") else f"region-{location.lower()}"

    query = f"""
        SELECT
          COUNT(*) AS partition_count,
          MIN(partition_id) AS min_partition_id,
          MAX(partition_id) AS max_partition_id,
          SUM(total_rows) AS total_rows,
          SUM(total_logical_bytes) AS total_logical_bytes
        FROM `{project}.{region}.INFORMATION_SCHEMA.PARTITIONS`
        WHERE table_name = @table_name
          AND table_schema = @dataset
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("table_name", "STRING", table),
            bigquery.ScalarQueryParameter("dataset", "STRING", dataset),
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
