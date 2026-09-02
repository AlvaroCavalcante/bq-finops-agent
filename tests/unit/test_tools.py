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
from unittest.mock import MagicMock, patch

from app.callbacks import after_tool_callback, before_tool_callback
from app.tools.compare import compare_query_stats, validate_output_schema
from app.tools.measure_cost import list_referenced_tables, measure_query_cost
from app.tools.metadata import get_partition_summary, get_table_metadata
from app.tools.safety import is_readonly_select


class _FakeContext:
    def __init__(self) -> None:
        self.state: dict = {}


def _persist(name: str, args: dict, ctx: _FakeContext, response: dict) -> dict:
    after_tool_callback(
        tool=SimpleNamespace(name=name),
        args=args,
        tool_context=ctx,
        tool_response=response,
    )
    return response


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
    ctx = _FakeContext()
    original = [{"name": "a", "type": "INT64", "mode": "NULLABLE"}]
    optimized = [{"name": "a", "type": "INT64", "mode": "NULLABLE"}]
    result = _persist(
        "validate_output_schema",
        {"original_schema": original, "optimized_schema": optimized},
        ctx,
        validate_output_schema(original, optimized),
    )
    assert result["passed"] is True
    assert ctx.state["schema_check"]["passed"] is True


def test_validate_output_schema_fails_on_rename() -> None:
    result = validate_output_schema(
        [{"name": "a", "type": "INT64"}],
        [{"name": "b", "type": "INT64"}],
    )
    assert result["passed"] is False


def test_compare_query_stats_savings() -> None:
    ctx = _FakeContext()
    result = _persist(
        "compare_query_stats",
        {
            "baseline_bytes": 1000,
            "optimized_bytes": 400,
            "baseline_method": "dry_run",
            "optimized_method": "live",
        },
        ctx,
        compare_query_stats(
            1000,
            400,
            baseline_method="dry_run",
            optimized_method="live",
        ),
    )
    assert result["bytes_saved"] == 600
    assert result["savings_pct"] == 60.0
    assert result["measurement_method"] == "mixed"
    assert ctx.state["compare"]["bytes_saved"] == 600


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
    sql = "SELECT 1 FROM `proj.ds.tbl`"
    job = _fake_job(tables=[("proj", "ds", "tbl")])
    with patch("app.tools.measure_cost._run_query", return_value=job) as mock_run:
        result = _persist(
            "list_referenced_tables",
            {"sql": sql},
            ctx,
            list_referenced_tables(sql),
        )
    mock_run.assert_called_once_with(sql, dry_run=True)
    assert result["status"] == "success"
    assert ctx.state["referenced_tables"] == [
        {"project": "proj", "dataset": "ds", "table": "tbl"}
    ]
    assert ctx.state["discovery_sql"] == sql
    assert ctx.state["discovery_dry_run"]["total_bytes_processed"] == 100


def test_list_referenced_tables_uses_sql_argument_not_state() -> None:
    job = _fake_job(tables=[("proj", "ds", "tbl")])
    with patch("app.tools.measure_cost._run_query", return_value=job) as mock_run:
        list_referenced_tables("SELECT 1 FROM `proj.ds.tbl`")
    mock_run.assert_called_once_with("SELECT 1 FROM `proj.ds.tbl`", dry_run=True)


def test_list_referenced_tables_error_does_not_invent_tables() -> None:
    ctx = _FakeContext()
    with patch(
        "app.tools.measure_cost._run_query",
        side_effect=RuntimeError("syntax error"),
    ):
        result = _persist(
            "list_referenced_tables",
            {"sql": "SELEKT"},
            ctx,
            list_referenced_tables("SELEKT"),
        )
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
        result = _persist(
            "measure_query_cost",
            {"sql": sql, "purpose": "baseline"},
            ctx,
            measure_query_cost(sql, "baseline", ctx),
        )
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
        result = _persist(
            "measure_query_cost",
            {"sql": "SELECT 2", "purpose": "optimized_final"},
            ctx,
            measure_query_cost("SELECT 2", "optimized_final", ctx),
        )
    mock_run.assert_called_once_with("SELECT 2", dry_run=False)
    assert result["measurement_method"] == "live"
    assert ctx.state["optimized_verified_live"] is True


def test_measure_query_cost_dry_run_error_does_not_live() -> None:
    ctx = _FakeContext()
    with patch(
        "app.tools.measure_cost._run_query",
        side_effect=RuntimeError("invalid SQL"),
    ) as mock_run:
        result = _persist(
            "measure_query_cost",
            {"sql": "SELECT 1 FROM nope", "purpose": "baseline"},
            ctx,
            measure_query_cost("SELECT 1 FROM nope", "baseline", ctx),
        )
    mock_run.assert_called_once_with("SELECT 1 FROM nope", dry_run=True)
    assert result["status"] == "error"
    assert ctx.state["baseline_measured"] is False


def test_measure_query_cost_live_fallback_when_bytes_missing() -> None:
    ctx = _FakeContext()
    dry_job = _fake_job(bytes_processed=None)
    live_job = _fake_job(bytes_processed=40)

    def _side_effect(_sql: str, dry_run: bool):
        return dry_job if dry_run else live_job

    with patch("app.tools.measure_cost._run_query", side_effect=_side_effect) as mock_run:
        result = _persist(
            "measure_query_cost",
            {"sql": "SELECT 1", "purpose": "optimized_probe"},
            ctx,
            measure_query_cost("SELECT 1", "optimized_probe", ctx),
        )
    assert mock_run.call_count == 2
    assert result["measurement_method"] == "mixed"
    assert result["total_bytes_processed"] == 40
    assert ctx.state["optimized_verified_live"] is True


def test_measure_query_cost_baseline_requires_table_metadata() -> None:
    ctx = _FakeContext()
    ctx.state["referenced_tables"] = [
        {"project": "proj", "dataset": "ds", "table": "tbl"},
    ]
    with patch("app.tools.measure_cost._run_query") as mock_run:
        result = _persist(
            "measure_query_cost",
            {"sql": "SELECT 1 FROM `proj.ds.tbl`", "purpose": "baseline"},
            ctx,
            measure_query_cost("SELECT 1 FROM `proj.ds.tbl`", "baseline", ctx),
        )
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
        result = _persist(
            "measure_query_cost",
            {"sql": "SELECT 1 FROM `proj.ds.tbl`", "purpose": "baseline"},
            ctx,
            measure_query_cost("SELECT 1 FROM `proj.ds.tbl`", "baseline", ctx),
        )
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
        result = _persist(
            "get_table_metadata",
            {"project": "p", "dataset": "d", "table": "t"},
            ctx,
            get_table_metadata("p", "d", "t"),
        )
    assert result["status"] == "success"
    assert ctx.state["has_clustering"] is True
    assert "p.d.t" in ctx.state["tables_metadata"]


def test_get_partition_summary_queries_dataset_information_schema() -> None:
    ctx = _FakeContext()
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
        result = _persist(
            "get_partition_summary",
            {
                "project": "gc-pr-dl-001",
                "dataset": "dtl_bl_refined",
                "table": "TB_CARTOES_HIST_DIA",
            },
            ctx,
            get_partition_summary(
                "gc-pr-dl-001", "dtl_bl_refined", "TB_CARTOES_HIST_DIA"
            ),
        )
    assert result["status"] == "success"
    assert result["partition_count"] == 52
    sql = client.query.call_args.args[0]
    assert "dtl_bl_refined.INFORMATION_SCHEMA.PARTITIONS" in sql
    assert "region-" not in sql
    assert (
        ctx.state["partition_summaries"][
            "gc-pr-dl-001.dtl_bl_refined.TB_CARTOES_HIST_DIA"
        ]["partition_count"]
        == 52
    )


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
        result = _persist(
            "get_table_metadata",
            {"project": "p", "dataset": "d", "table": "t"},
            ctx,
            get_table_metadata("p", "d", "t"),
        )
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
        result = _persist(
            "get_table_metadata",
            {"project": "p", "dataset": "d", "table": "orders"},
            ctx,
            get_table_metadata("p", "d", "orders"),
        )
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


def test_after_tool_callback_skips_non_dict_response() -> None:
    ctx = _FakeContext()
    after_tool_callback(
        tool=SimpleNamespace(name="list_referenced_tables"),
        args={"sql": "SELECT 1"},
        tool_context=ctx,
        tool_response="not-a-dict",
    )
    assert ctx.state == {}
