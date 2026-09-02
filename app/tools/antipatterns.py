from __future__ import annotations

import re

import sqlglot
from google.adk.tools import ToolContext
from sqlglot import exp

from app.schemas import AntipatternResult, dump_model

_WILDCARD_TABLE = re.compile(r"`[^`]+\.\*`|\b\w+\.\*`", re.IGNORECASE)


def detect_antipatterns(sql: str, metadata_map: dict) -> dict:
    """Detect BigQuery SQL anti-patterns from SQL and a metadata map."""
    findings: list[dict] = []

    if re.search(r"SELECT\s+\*", sql, re.IGNORECASE):
        findings.append(
            {
                "pattern": "select_star",
                "severity": "high",
                "message": "SELECT * scans all columns; project only needed columns.",
            }
        )

    if _WILDCARD_TABLE.search(sql):
        findings.append(
            {
                "pattern": "broad_wildcard",
                "severity": "high",
                "message": "Broad wildcard table reference; use the most granular prefix.",
            }
        )

    if re.search(r"\bUNION\b(?!\s+ALL)", sql, re.IGNORECASE):
        findings.append(
            {
                "pattern": "union_distinct",
                "severity": "medium",
                "message": "UNION deduplicates rows (shuffle cost); use UNION ALL if duplicates OK.",
            }
        )

    if re.search(r"\bCROSS\s+JOIN\b", sql, re.IGNORECASE):
        findings.append(
            {
                "pattern": "cross_join",
                "severity": "high",
                "message": "CROSS JOIN can explode row counts; verify necessity.",
            }
        )

    try:
        parsed = sqlglot.parse_one(sql, read="bigquery")
    except Exception as exc:  # noqa: BLE001
        findings.append(
            {
                "pattern": "parse_error",
                "severity": "warning",
                "message": f"SQL parse failed: {exc}",
            }
        )
        return dump_model(AntipatternResult, {"status": "success", "findings": findings})

    for table_meta in metadata_map.values():
        if not isinstance(table_meta, dict):
            continue
        if table_meta.get("status") == "error":
            continue
        tp = table_meta.get("time_partitioning") or {}
        if not isinstance(tp, dict):
            tp = {}
        part_field = tp.get("field")
        if not part_field and tp.get("type"):
            part_field = "_PARTITIONTIME"
        if part_field and part_field not in sql:
            if not re.search(r"_PARTITIONTIME|_PARTITIONDATE", sql, re.IGNORECASE):
                findings.append(
                    {
                        "pattern": "missing_partition_filter",
                        "severity": "high",
                        "message": (
                            f"Table {table_meta.get('dataset')}.{table_meta.get('table')} "
                            f"is partitioned on '{part_field}' but query may lack partition filter."
                        ),
                    }
                )

        rp = table_meta.get("range_partitioning") or {}
        if isinstance(rp, dict) and rp.get("field"):
            range_field = rp["field"]
            if range_field not in sql:
                findings.append(
                    {
                        "pattern": "missing_partition_filter",
                        "severity": "high",
                        "message": (
                            f"Table {table_meta.get('dataset')}.{table_meta.get('table')} "
                            f"is range-partitioned on '{range_field}' but query may lack "
                            "partition filter."
                        ),
                    }
                )

        for cluster_col in table_meta.get("clustering_fields") or []:
            if cluster_col.lower() not in sql.lower():
                findings.append(
                    {
                        "pattern": "clustering_column_unused",
                        "severity": "medium",
                        "message": (
                            f"Clustering column '{cluster_col}' not referenced in filters/joins."
                        ),
                    }
                )

        pk_cols = table_meta.get("primary_key_columns") or []
        fks = table_meta.get("foreign_keys") or []
        has_join = bool(re.search(r"\bJOIN\b", sql, re.IGNORECASE))

        if has_join and (pk_cols or fks):
            findings.append(
                {
                    "pattern": "key_constraints_available",
                    "severity": "medium",
                    "message": (
                        f"Table {table_meta.get('dataset')}.{table_meta.get('table')} "
                        f"declares primary_key_columns={list(pk_cols)} and "
                        f"{len(fks)} foreign key(s); consider join elimination "
                        "when columns from the joined table are unused."
                    ),
                }
            )

    for func in parsed.find_all(exp.Anonymous, exp.Func):
        parent = func.parent
        if isinstance(parent, exp.Where):
            func_sql = func.sql(dialect="bigquery")
            if any(
                p in func_sql.upper()
                for p in ("DATE(", "TIMESTAMP(", "EXTRACT(", "FORMAT_", "CAST(")
            ):
                findings.append(
                    {
                        "pattern": "function_on_filter",
                        "severity": "medium",
                        "message": (
                            f"Function on filter may prevent partition pruning: {func_sql}"
                        ),
                    }
                )
                break

    return dump_model(AntipatternResult, {"status": "success", "findings": findings})


def detect_sql_antipatterns(
    sql: str,
    tool_context: ToolContext,
) -> dict:
    """Detect BigQuery SQL anti-patterns using table metadata from session state.

    Args:
        sql: BigQuery SQL to analyze. Pass original_sql from the pipeline context.

    Returns:
        dict listing detected anti-patterns with severity and suggestion.
    """
    if not sql:
        return dump_model(
            AntipatternResult,
            {
                "status": "error",
                "findings": [],
                "error_message": "No SQL provided.",
            },
        )

    metadata_map = dict(tool_context.state.get("tables_metadata") or {})
    return detect_antipatterns(sql, metadata_map)
