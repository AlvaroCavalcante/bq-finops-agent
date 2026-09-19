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

from types import SimpleNamespace

from app.callbacks import after_tool_callback
from app.tools.antipatterns import detect_antipatterns, detect_sql_antipatterns


class _FakeContext:
    def __init__(self) -> None:
        self.state: dict = {}


def test_detect_select_star() -> None:
    result = detect_antipatterns("SELECT * FROM t", {})
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
    result = detect_antipatterns(sql, meta)
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
    result = detect_antipatterns("SELECT order_id FROM orders", meta)
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
    result = detect_antipatterns(sql, meta)
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
    result = detect_antipatterns(sql, meta)
    patterns = {f["pattern"] for f in result["findings"]}
    assert "missing_partition_filter" in patterns


def test_unpartitioned_table_without_status_is_not_flagged() -> None:
    sql = "SELECT id FROM t"
    meta = {"p.d.t": {"dataset": "d", "table": "t", "clustering_fields": []}}
    result = detect_antipatterns(sql, meta)
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
    result = detect_sql_antipatterns("SELECT id FROM t", tool_context=ctx)
    after_tool_callback(
        tool=SimpleNamespace(name="detect_sql_antipatterns"),
        args={"sql": "SELECT id FROM t"},
        tool_context=ctx,
        tool_response=result,
    )
    patterns = {f["pattern"] for f in result["findings"]}
    assert "missing_partition_filter" in patterns
    assert ctx.state["antipatterns"]


def _year_part_meta() -> dict:
    return {
        "p.d.t": {
            "status": "success",
            "dataset": "d",
            "table": "t",
            "time_partitioning": {"field": "DATA", "type": "YEAR"},
        }
    }


def test_merge_column_equality_is_not_a_partition_filter() -> None:
    sql = (
        "MERGE t AS tgt USING (SELECT id, DATA FROM aux) src "
        "ON tgt.id = src.id AND tgt.DATA = src.DATA "
        "WHEN MATCHED THEN UPDATE SET tgt.DATA = src.DATA "
        "WHEN NOT MATCHED THEN INSERT (id, DATA) VALUES (src.id, src.DATA)"
    )
    result = detect_antipatterns(sql, _year_part_meta())
    patterns = {f["pattern"] for f in result["findings"]}
    assert "missing_partition_filter" in patterns
    assert "constant" in " ".join(f["message"] for f in result["findings"]).lower()


def test_merge_with_constant_grain_filter_is_not_flagged() -> None:
    sql = (
        "MERGE t AS tgt USING (SELECT id, DATA FROM aux) src "
        "ON tgt.id = src.id AND tgt.DATA = src.DATA "
        "AND tgt.DATA >= DATE_TRUNC(CURRENT_DATE(), YEAR) "
        "AND tgt.DATA < DATE_ADD(DATE_TRUNC(CURRENT_DATE(), YEAR), INTERVAL 1 YEAR) "
        "WHEN MATCHED THEN UPDATE SET tgt.v = src.v "
        "WHEN NOT MATCHED THEN INSERT (id, DATA) VALUES (src.id, src.DATA)"
    )
    result = detect_antipatterns(sql, _year_part_meta())
    patterns = {f["pattern"] for f in result["findings"]}
    assert "missing_partition_filter" not in patterns


def test_insert_without_constant_filter_is_flagged() -> None:
    sql = "INSERT INTO t (id, DATA) SELECT id, DATE(DATAS) AS DATA FROM aux"
    result = detect_antipatterns(sql, _year_part_meta())
    patterns = {f["pattern"] for f in result["findings"]}
    assert "missing_partition_filter" in patterns


def test_insert_with_constant_grain_filter_is_not_flagged() -> None:
    sql = (
        "INSERT INTO t (id, DATA) "
        "SELECT id, DATE(DATAS) AS DATA FROM aux "
        "WHERE DATE(DATAS) >= DATE_TRUNC(CURRENT_DATE(), YEAR)"
    )
    result = detect_antipatterns(sql, _year_part_meta())
    patterns = {f["pattern"] for f in result["findings"]}
    assert "missing_partition_filter" not in patterns
