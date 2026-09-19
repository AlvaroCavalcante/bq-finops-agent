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

from app.schemas import OptimizationReport
from app.tools.report import (
    COMPARE_MISSING_WARNING,
    build_report,
    render_markdown,
    render_report,
)

ORIGINAL_SQL = "SELECT * FROM `p.d.t`"
OPTIMIZED_SQL = "SELECT a FROM `p.d.t` WHERE dt = CURRENT_DATE()"
PLATEAU_WARNING = "Further rewrites did not beat the 5% byte-reduction threshold."
METADATA_WARNING = "VM_CONTAS_NOVA is a view; partition metadata is unavailable."


def _ctx(**state):
    return SimpleNamespace(state=state)


def _compare(**extra) -> dict:
    compare = {
        "status": "success",
        "baseline_bytes": 23490216531,
        "optimized_bytes": 17891222049,
        "bytes_saved": 5598994482,
        "savings_pct": 23.84,
        "estimated_usd_before": 0.133526,
        "estimated_usd_after": 0.1017,
        "usd_saved_per_run": 0.031826,
        "measurement_method": "dry_run",
        "warnings": [],
    }
    compare.update(extra)
    return compare


def _state(**extra) -> dict:
    state = {
        "original_sql": ORIGINAL_SQL,
        "optimizer_step": {
            "optimized_sql": OPTIMIZED_SQL,
            "changes_applied": [
                {
                    "category": "partition_pruning",
                    "description": "Added a filter on dt.",
                }
            ],
            "recommendations_not_applied": [
                {
                    "category": "clustering",
                    "description": "Table is not clustered.",
                }
            ],
        },
        "validation": {
            "passed": True,
            "compare": _compare(),
            "warnings": [PLATEAU_WARNING],
            "retry_hint": None,
            "baseline_measured": True,
            "optimized_verified_live": False,
        },
        "metadata_step": {"warnings": [METADATA_WARNING]},
    }
    state.update(extra)
    return state


def test_build_report_maps_state_onto_schema() -> None:
    report = build_report(_ctx(**_state()))

    assert report.status == "success"
    assert report.error_code is None
    assert report.error_message is None
    assert report.optimized_sql == OPTIMIZED_SQL
    assert report.baseline_bytes == 23490216531
    assert report.optimized_bytes == 17891222049
    assert report.bytes_saved == 5598994482
    assert report.savings_pct == 23.84
    assert report.usd_saved_per_run == 0.031826
    assert report.measurement_method == "dry_run"
    assert report.baseline_measured is True
    assert report.optimized_verified_live is False
    assert report.changes_applied[0].category == "partition_pruning"
    assert report.recommendations_not_applied[0].category == "clustering"


def test_build_report_merges_warnings_without_duplicates() -> None:
    state = _state()
    state["validation"]["warnings"] = [PLATEAU_WARNING, METADATA_WARNING]

    report = build_report(_ctx(**state))

    assert report.warnings == [PLATEAU_WARNING, METADATA_WARNING]


def test_build_report_falls_back_when_compare_is_missing() -> None:
    state = _state()
    state["validation"]["compare"] = {
        "status": "error",
        "error_message": "compare_query_stats was not run.",
    }

    report = build_report(_ctx(**state))

    assert report.baseline_bytes == 0
    assert report.optimized_bytes == 0
    assert report.bytes_saved == 0
    assert report.savings_pct == 0.0
    assert report.usd_saved_per_run == 0.0
    assert report.measurement_method == "dry_run"
    assert COMPARE_MISSING_WARNING in report.warnings


def test_build_report_falls_back_to_original_sql() -> None:
    report = build_report(_ctx(**_state(optimizer_step={})))

    assert report.optimized_sql == ORIGINAL_SQL


def test_build_report_reads_measurement_method_from_measurements() -> None:
    state = _state(optimized_measurement={"measurement_method": "live"})
    state["validation"]["compare"] = _compare(measurement_method=None)

    report = build_report(_ctx(**state))

    assert report.measurement_method == "live"


def test_render_markdown_highlights_savings_and_sql() -> None:
    markdown = render_markdown(build_report(_ctx(**_state())))

    assert markdown.startswith("# BigQuery optimization report")
    assert "23.84% fewer bytes processed" in markdown
    assert "21.88 GiB -> 16.66 GiB" in markdown
    assert "$0.1335 -> $0.1017" in markdown
    assert "$0.0318 saved" in markdown
    assert f"```sql\n{OPTIMIZED_SQL}\n```" in markdown
    assert "- **partition_pruning** — Added a filter on dt." in markdown
    assert "- **clustering** — Table is not clustered." in markdown
    assert f"- {PLATEAU_WARNING}" in markdown
    assert f"- {METADATA_WARNING}" in markdown
    assert "- Method: `dry_run`" in markdown
    assert "- Baseline measured: yes" in markdown
    assert "- Optimized verified live: no" in markdown


def test_render_markdown_floors_negligible_usd() -> None:
    state = _state()
    state["validation"]["compare"] = _compare(
        estimated_usd_before=0.00009,
        estimated_usd_after=0.00002,
        usd_saved_per_run=0.00007,
    )

    markdown = render_markdown(build_report(_ctx(**state)))

    assert "< $0.0001 -> < $0.0001 (< $0.0001 saved)" in markdown


def test_render_markdown_without_savings() -> None:
    state = _state()
    state["validation"]["compare"] = _compare(
        optimized_bytes=23490216531,
        bytes_saved=0,
        savings_pct=0.0,
        estimated_usd_after=0.133526,
        usd_saved_per_run=0.0,
    )

    markdown = render_markdown(build_report(_ctx(**state)))

    assert "No byte reduction found" in markdown
    assert "fewer bytes processed" not in markdown
    assert "Bytes processed: 21.88 GiB" in markdown


def test_render_markdown_omits_empty_sections() -> None:
    state = _state(optimizer_step={"optimized_sql": OPTIMIZED_SQL})
    state["validation"]["warnings"] = []
    state["metadata_step"] = {}

    markdown = render_markdown(build_report(_ctx(**state)))

    assert "## Changes applied" not in markdown
    assert "## Recommendations not applied" not in markdown
    assert "## Warnings" not in markdown
    assert "## Measurement" in markdown


def test_render_report_emits_markdown_and_structured_output() -> None:
    event = render_report(_ctx(**_state()))

    assert event.content is not None
    assert event.content.role == "model"
    assert event.content.parts[0].text.startswith("# BigQuery optimization report")

    assert OptimizationReport.model_validate(event.output).optimized_sql == (
        OPTIMIZED_SQL
    )
    assert event.actions.state_delta["optimization_report"] == event.output


def test_build_report_aborts_with_pipeline_error() -> None:
    report = build_report(
        _ctx(
            original_sql="MERGE t USING s ON FALSE WHEN NOT MATCHED THEN INSERT ROW",
            pipeline_error={
                "error_code": "FORBIDDEN_SQL",
                "message": "Only SELECT / WITH … SELECT queries are allowed.",
            },
        )
    )

    assert report.status == "error"
    assert report.error_code == "FORBIDDEN_SQL"
    assert report.error_message == (
        "Only SELECT / WITH … SELECT queries are allowed."
    )
    assert report.optimized_sql.startswith("MERGE")
    assert report.baseline_bytes == 0
    assert report.bytes_saved == 0


def test_render_markdown_for_pipeline_error() -> None:
    report = build_report(
        _ctx(
            original_sql="DELETE FROM t",
            pipeline_error={
                "error_code": "FORBIDDEN_SQL",
                "message": "Only SELECT / WITH … SELECT queries are allowed.",
            },
        )
    )
    markdown = render_markdown(report)

    assert markdown.startswith("# Could not optimize this query")
    assert "**Error code:** `FORBIDDEN_SQL`" in markdown
    assert "Only SELECT / WITH … SELECT queries are allowed." in markdown
    assert "The pipeline stopped before optimization." in markdown
    assert "BigQuery optimization report" not in markdown


def test_render_report_emits_error_markdown() -> None:
    event = render_report(
        _ctx(
            original_sql="SELECT 1 FROM missing",
            pipeline_error={
                "error_code": "BASELINE_FAILED",
                "message": "Not found: table missing",
            },
        )
    )

    assert event.content.parts[0].text.startswith("# Could not optimize this query")
    assert event.output["status"] == "error"
    assert event.output["error_code"] == "BASELINE_FAILED"
    assert event.output["error_message"] == "Not found: table missing"
