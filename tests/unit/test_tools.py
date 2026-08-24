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

import pytest

from app.callbacks import before_tool_callback
from app.tools.compare import compare_query_stats, validate_output_schema
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
