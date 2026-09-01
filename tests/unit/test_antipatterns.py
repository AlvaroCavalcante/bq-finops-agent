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

from app.tools.antipatterns import _detect_sql_antipatterns, detect_sql_antipatterns


class _FakeContext:
    def __init__(self) -> None:
        self.state: dict = {}


def test_detect_select_star() -> None:
    result = _detect_sql_antipatterns("SELECT * FROM t", {})
    patterns = {f["pattern"] for f in result["findings"]}
    assert "select_star" in patterns


def test_detect_key_constraints_available_on_join() -> None:
    meta = {
        "p.d.orders": {
            "status": "success",
            "dataset": "d",
            "table": "orders",
            "primary_key_columns": ["order_id"],
            "foreign_keys": [
                {
                    "name": "fk_customer",
                    "referenced_table": "p.d.customers",
                    "column_references": [
                        {
                            "referencing_column": "customer_id",
                            "referenced_column": "id",
                        }
                    ],
                }
            ],
        }
    }
    sql = (
        "SELECT o.order_id FROM orders o "
        "JOIN customers c ON o.customer_id = c.id"
    )
    result = _detect_sql_antipatterns(sql, meta)
    patterns = {f["pattern"] for f in result["findings"]}
    assert "key_constraints_available" in patterns


def test_key_constraints_not_flagged_without_join() -> None:
    meta = {
        "p.d.orders": {
            "status": "success",
            "dataset": "d",
            "table": "orders",
            "primary_key_columns": ["order_id"],
            "foreign_keys": [],
        }
    }
    result = _detect_sql_antipatterns("SELECT order_id FROM orders", meta)
    patterns = {f["pattern"] for f in result["findings"]}
    assert "key_constraints_available" not in patterns


def test_missing_status_still_detects_partition_filter() -> None:
    """LLM-copied metadata without status must not skip partition analysis."""
    sql = "SELECT id FROM t"
    meta = {
        "p.d.t": {
            "dataset": "d",
            "table": "t",
            "time_partitioning": {"field": "DATA_VISAO", "type": "DAY"},
        }
    }
    result = _detect_sql_antipatterns(sql, meta)
    patterns = {f["pattern"] for f in result["findings"]}
    assert "missing_partition_filter" in patterns


def test_full_table_metadata_detects_missing_partition_filter() -> None:
    sql = "SELECT id FROM cartoes"
    meta = {
        "p.d.cartoes": {
            "status": "success",
            "project": "p",
            "dataset": "d",
            "table": "cartoes",
            "time_partitioning": {
                "type": "DAY",
                "field": "DATA_VISAO",
                "require_partition_filter": False,
            },
        }
    }
    result = _detect_sql_antipatterns(sql, meta)
    patterns = {f["pattern"] for f in result["findings"]}
    assert "missing_partition_filter" in patterns


def test_unpartitioned_table_without_status_is_not_flagged() -> None:
    sql = "SELECT id FROM t"
    meta = {"p.d.t": {"dataset": "d", "table": "t", "clustering_fields": []}}
    result = _detect_sql_antipatterns(sql, meta)
    patterns = {f["pattern"] for f in result["findings"]}
    assert "missing_partition_filter" not in patterns


def test_detect_sql_antipatterns_reads_tables_metadata_from_state() -> None:
    ctx = _FakeContext()
    ctx.state["original_sql"] = "SELECT id FROM t"
    ctx.state["tables_metadata"] = {
        "p.d.t": {
            "status": "success",
            "dataset": "d",
            "table": "t",
            "time_partitioning": {"field": "DATA_VISAO", "type": "DAY"},
        }
    }
    result = detect_sql_antipatterns(tool_context=ctx)
    patterns = {f["pattern"] for f in result["findings"]}
    assert "missing_partition_filter" in patterns
    assert ctx.state["antipatterns"]
