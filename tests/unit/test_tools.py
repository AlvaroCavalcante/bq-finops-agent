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

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.callbacks import before_tool_callback
from app.tools.compare import compare_query_stats, validate_output_schema
from app.tools.measure_cost import list_referenced_tables, measure_query_cost
from app.tools.metadata import get_partition_summary, get_table_metadata
from app.tools.safety import is_readonly_select


def test_readonly_select_accepts_with_clause() -> None:
    sql = "WITH cte AS (SELECT 1 AS x) SELECT x FROM cte"
    ok, err = is_readonly_select(sql)
    assert ok is True
    assert err is None


def test_readonly_select_rejects_delete() -> None:
    sql = "DELETE FROM dataset.table WHERE id = 1"
    ok, err = is_readonly_select(sql)
    assert ok is False
    assert err is not None


def test_before_tool_callback_allows_select() -> None:
    result = before_tool_callback(
        tool=None,
        args={"sql": "SELECT 1", "purpose": "baseline"},
        tool_context=None,
    )
    assert result is None


def test_before_tool_callback_blocks_dml() -> None:
    result = before_tool_callback(
        tool=None,
        args={"sql": "DELETE FROM dataset.table WHERE id = 1", "purpose": "baseline"},
        tool_context=None,
    )
    assert result is not None
    assert result["status"] == "error"
    assert "error_message" in result


def test_before_tool_callback_skips_tools_without_sql_args() -> None:
    result = before_tool_callback(
        tool=None,
        args={"project": "p", "dataset": "d", "table": "t"},
        tool_context=None,
    )
    assert result is None


def test_validate_output_schema_passes_matching_columns() -> None:
    original = json.dumps([{"name": "a", "type": "INT64", "mode": "NULLABLE"}])
    optimized = json.dumps([{"name": "a", "type": "INT64", "mode": "NULLABLE"}])
    result = validate_output_schema(original, optimized)
    assert result["passed"] is True


def test_validate_output_schema_fails_on_rename() -> None:
    original = json.dumps([{"name": "a", "type": "INT64"}])
    optimized = json.dumps([{"name": "b", "type": "INT64"}])
    result = validate_output_schema(original, optimized)
    assert result["passed"] is False


def test_compare_query_stats_savings() -> None:
    before = json.dumps({"total_bytes_processed": 1000, "measurement_method": "dry_run"})
    after = json.dumps({"total_bytes_processed": 400, "measurement_method": "live"})
    result = compare_query_stats(before, after)
    assert result["bytes_saved"] == 600
    assert result["savings_pct"] == 60.0
    assert result["measurement_method"] == "mixed"


class _FakeContext:
    def __init__(self) -> None:
        self.state: dict = {}


def _fake_job(*, bytes_processed=100, tables=None):
    refs = [
        SimpleNamespace(project=p, dataset_id=d, table_id=t)
        for p, d, t in (tables or [])
    ]
    return SimpleNamespace(
        total_bytes_processed=bytes_processed,
        referenced_tables=refs,
        schema=None,
        statement_type="SELECT",
        job_id="job-1",
    )


def test_list_referenced_tables_writes_state() -> None:
    ctx = _FakeContext()
    job = _fake_job(tables=[("proj", "ds", "tbl")])
    with patch("app.tools.measure_cost._run_query", return_value=job) as mock_run:
        result = list_referenced_tables("SELECT 1 FROM `proj.ds.tbl`", ctx)
    mock_run.assert_called_once_with("SELECT 1 FROM `proj.ds.tbl`", dry_run=True)
    assert result["status"] == "success"
    assert ctx.state["referenced_tables"] == [
        {"project": "proj", "dataset": "ds", "table": "tbl"}
    ]
    assert ctx.state["discovery_sql"] == "SELECT 1 FROM `proj.ds.tbl`"
    assert ctx.state["discovery_dry_run"]["total_bytes_processed"] == 100


def test_list_referenced_tables_error_does_not_invent_tables() -> None:
    ctx = _FakeContext()
    with patch(
        "app.tools.measure_cost._run_query",
        side_effect=RuntimeError("syntax error"),
    ):
        result = list_referenced_tables("SELEKT", ctx)
    assert result["status"] == "error"
    assert "referenced_tables" not in ctx.state
    assert "discovery_dry_run" not in ctx.state


def test_measure_query_cost_reuses_discovery_cache() -> None:
    ctx = _FakeContext()
    sql = "SELECT 1"
    ctx.state["discovery_sql"] = sql
    ctx.state["discovery_dry_run"] = {
        "status": "success",
        "total_bytes_processed": 50,
        "measurement_method": "dry_run",
        "referenced_tables": [],
        "output_schema": [],
        "statement_type": "SELECT",
        "job_id": "cached",
    }
    with patch("app.tools.measure_cost._run_query") as mock_run:
        result = measure_query_cost(sql, "baseline", ctx)
    mock_run.assert_not_called()
    assert result["measurement_method"] == "dry_run"
    assert result["total_bytes_processed"] == 50
    assert ctx.state["baseline_measured"] is True


def test_measure_query_cost_live_only_when_clustered() -> None:
    ctx = _FakeContext()
    ctx.state["has_clustering"] = True
    job = _fake_job(bytes_processed=200)
    with patch("app.tools.measure_cost._run_query", return_value=job) as mock_run:
        result = measure_query_cost("SELECT 1", "baseline", ctx)
    mock_run.assert_called_once_with("SELECT 1", dry_run=False)
    assert result["measurement_method"] == "live"
    assert result["total_bytes_processed"] == 200


def test_measure_query_cost_optimized_final_is_live_only() -> None:
    ctx = _FakeContext()
    job = _fake_job(bytes_processed=80)
    with patch("app.tools.measure_cost._run_query", return_value=job) as mock_run:
        result = measure_query_cost("SELECT 2", "optimized_final", ctx)
    mock_run.assert_called_once_with("SELECT 2", dry_run=False)
    assert result["measurement_method"] == "live"
    assert ctx.state["optimized_verified_live"] is True


def test_measure_query_cost_dry_run_error_does_not_live() -> None:
    ctx = _FakeContext()
    with patch(
        "app.tools.measure_cost._run_query",
        side_effect=RuntimeError("invalid SQL"),
    ) as mock_run:
        result = measure_query_cost("SELEKT", "baseline", ctx)
    mock_run.assert_called_once_with("SELEKT", dry_run=True)
    assert result["status"] == "error"
    assert ctx.state["baseline_measured"] is False


def test_measure_query_cost_live_fallback_when_bytes_missing() -> None:
    ctx = _FakeContext()
    dry_job = _fake_job(bytes_processed=None)
    live_job = _fake_job(bytes_processed=40)

    def _side_effect(_sql: str, dry_run: bool):
        return dry_job if dry_run else live_job

    with patch("app.tools.measure_cost._run_query", side_effect=_side_effect) as mock_run:
        result = measure_query_cost("SELECT 1", "optimized_probe", ctx)
    assert mock_run.call_count == 2
    assert result["measurement_method"] == "mixed"
    assert result["total_bytes_processed"] == 40


def test_measure_query_cost_baseline_requires_table_metadata() -> None:
    ctx = _FakeContext()
    ctx.state["referenced_tables"] = [
        {"project": "proj", "dataset": "ds", "table": "tbl"},
    ]
    with patch("app.tools.measure_cost._run_query") as mock_run:
        result = measure_query_cost("SELECT 1 FROM `proj.ds.tbl`", "baseline", ctx)
    mock_run.assert_not_called()
    assert result["status"] == "error"
    assert "get_table_metadata" in result["error_message"]
    assert "proj.ds.tbl" in result["error_message"]
    assert "baseline_measured" not in ctx.state


def test_measure_query_cost_baseline_live_after_clustered_metadata() -> None:
    ctx = _FakeContext()
    ctx.state["referenced_tables"] = [
        {"project": "proj", "dataset": "ds", "table": "tbl"},
    ]
    ctx.state["tables_metadata"] = {
        "proj.ds.tbl": {"status": "success", "clustering_fields": ["id"]},
    }
    ctx.state["has_clustering"] = True
    job = _fake_job(bytes_processed=200)
    with patch("app.tools.measure_cost._run_query", return_value=job) as mock_run:
        result = measure_query_cost("SELECT 1 FROM `proj.ds.tbl`", "baseline", ctx)
    mock_run.assert_called_once_with("SELECT 1 FROM `proj.ds.tbl`", dry_run=False)
    assert result["measurement_method"] == "live"
    assert result["total_bytes_processed"] == 200
    assert ctx.state["baseline_measured"] is True


def test_get_table_metadata_sets_has_clustering() -> None:
    ctx = _FakeContext()
    tbl = MagicMock()
    tbl.table_type = "TABLE"
    tbl.num_bytes = 10
    tbl.num_rows = 2
    tbl.time_partitioning = None
    tbl.range_partitioning = None
    tbl.clustering_fields = ["customer_id"]
    tbl.schema = []
    client = MagicMock()
    client.get_table.return_value = tbl
    with patch("app.tools.metadata.get_bq_client", return_value=client):
        result = get_table_metadata("p", "d", "t", ctx)
    assert result["status"] == "success"
    assert ctx.state["has_clustering"] is True
    assert "p.d.t" in ctx.state["tables_metadata"]


def test_get_partition_summary_queries_dataset_information_schema() -> None:
    row = SimpleNamespace(
        partition_count=52,
        min_partition_id="20240101",
        max_partition_id="20241231",
        total_rows=100,
        total_logical_bytes=200,
    )
    job = MagicMock()
    job.result.return_value = [row]
    client = MagicMock()
    client.query.return_value = job
    with patch("app.tools.metadata.get_bq_client", return_value=client):
        result = get_partition_summary("gc-pr-dl-001", "dtl_bl_refined", "TB_CARTOES_HIST_DIA")
    assert result["status"] == "success"
    assert result["partition_count"] == 52
    sql = client.query.call_args.args[0]
    assert "dtl_bl_refined.INFORMATION_SCHEMA.PARTITIONS" in sql
    assert "region-" not in sql


def test_get_table_metadata_without_clustering_sets_flag_false() -> None:
    ctx = _FakeContext()
    tbl = MagicMock()
    tbl.table_type = "TABLE"
    tbl.num_bytes = 10
    tbl.num_rows = 2
    tbl.time_partitioning = None
    tbl.range_partitioning = None
    tbl.clustering_fields = None
    tbl.schema = []
    client = MagicMock()
    client.get_table.return_value = tbl
    with patch("app.tools.metadata.get_bq_client", return_value=client):
        result = get_table_metadata("p", "d", "t", ctx)
    assert ctx.state["has_clustering"] is False
    assert result["primary_key_columns"] == []
    assert result["foreign_keys"] == []


def test_get_table_metadata_includes_primary_and_foreign_keys() -> None:
    ctx = _FakeContext()
    tbl = MagicMock()
    tbl.table_type = "TABLE"
    tbl.num_bytes = 10
    tbl.num_rows = 2
    tbl.time_partitioning = None
    tbl.range_partitioning = None
    tbl.clustering_fields = None
    tbl.schema = []
    tbl.table_constraints = SimpleNamespace(
        primary_key=SimpleNamespace(columns=["order_id"]),
        foreign_keys=[
            SimpleNamespace(
                name="fk_customer",
                referenced_table=SimpleNamespace(
                    project="p", dataset_id="d", table_id="customers"
                ),
                column_references=[
                    SimpleNamespace(
                        referencing_column="customer_id",
                        referenced_column="id",
                    )
                ],
            )
        ],
    )
    client = MagicMock()
    client.get_table.return_value = tbl
    with patch("app.tools.metadata.get_bq_client", return_value=client):
        result = get_table_metadata("p", "d", "orders", ctx)
    assert result["primary_key_columns"] == ["order_id"]
    assert result["foreign_keys"] == [
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
    ]
    assert ctx.state["tables_metadata"]["p.d.orders"]["primary_key_columns"] == [
        "order_id"
    ]
