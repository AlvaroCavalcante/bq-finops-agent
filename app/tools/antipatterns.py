# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from __future__ import annotations

import json
import re

import sqlglot
from sqlglot import exp

_WILDCARD_TABLE = re.compile(r"`[^`]+\.\*`|\b\w+\.\*`", re.IGNORECASE)


def detect_sql_antipatterns(sql: str, table_metadata_json: str) -> dict:
    """Detect BigQuery SQL anti-patterns deterministically.

    Args:
        sql: BigQuery SQL to analyze.
        table_metadata_json: JSON string mapping table refs to metadata dicts.

    Returns:
        dict listing detected anti-patterns with severity and suggestion.
    """
    findings: list[dict] = []
    metadata_map: dict = {}
    if table_metadata_json:
        try:
            metadata_map = json.loads(table_metadata_json)
        except json.JSONDecodeError:
            findings.append(
                {
                    "pattern": "invalid_metadata_json",
                    "severity": "warning",
                    "message": "Could not parse table_metadata_json.",
                }
            )

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
        return {"status": "success", "findings": findings}

    for table_meta in metadata_map.values():
        if table_meta.get("status") != "success":
            continue
        part_field = None
        tp = table_meta.get("time_partitioning") or {}
        part_field = tp.get("field") or "_PARTITIONTIME"
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

    return {"status": "success", "findings": findings}
