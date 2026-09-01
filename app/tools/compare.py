from __future__ import annotations

import difflib

from google.adk.tools import ToolContext

from app.schemas import CompareResult, SchemaCheck, dump_model
from app.tools.bq_client import get_usd_per_tib


def _schema_fields(measurement: dict | None) -> list[dict]:
    if not isinstance(measurement, dict):
        return []
    data = measurement.get("output_schema")
    return data if isinstance(data, list) else []


def _compare_column_schemas(original: list, optimized: list) -> dict:
    mismatches: list[str] = []
    if len(original) != len(optimized):
        mismatches.append(
            f"Column count differs: {len(original)} vs {len(optimized)}."
        )

    for i, (o, n) in enumerate(zip(original, optimized, strict=False)):
        if not isinstance(o, dict):
            o = {}
        if not isinstance(n, dict):
            n = {}
        if o.get("name") != n.get("name"):
            mismatches.append(
                f"Column {i}: name '{o.get('name')}' vs '{n.get('name')}'."
            )
        if o.get("type") != n.get("type"):
            mismatches.append(
                f"Column {i}: type '{o.get('type')}' vs '{n.get('type')}'."
            )

    return {
        "status": "success",
        "passed": len(mismatches) == 0,
        "mismatches": mismatches,
    }


def _compare_measurements(before: dict, after: dict) -> dict:
    baseline_bytes = int(before.get("total_bytes_processed") or 0)
    optimized_bytes = int(after.get("total_bytes_processed") or 0)
    bytes_saved = max(0, baseline_bytes - optimized_bytes)
    savings_pct = (
        round(100.0 * bytes_saved / baseline_bytes, 2) if baseline_bytes > 0 else 0.0
    )

    usd_per_tib = get_usd_per_tib()
    tib = 1024**4

    usd_before = round(baseline_bytes / tib * usd_per_tib, 6)
    usd_after = round(optimized_bytes / tib * usd_per_tib, 6)
    usd_saved = round(usd_before - usd_after, 6)

    before_method = before.get("measurement_method", "dry_run")
    after_method = after.get("measurement_method", "dry_run")
    if before_method == after_method:
        measurement_method = before_method
    else:
        measurement_method = "mixed"

    warnings: list[str] = []
    if before_method != after_method:
        warnings.append(
            f"Baseline measured via {before_method}, optimized via {after_method}."
        )
    if savings_pct == 0 and optimized_bytes >= baseline_bytes:
        warnings.append("No byte reduction detected.")

    return {
        "status": "success",
        "baseline_bytes": baseline_bytes,
        "optimized_bytes": optimized_bytes,
        "bytes_saved": bytes_saved,
        "savings_pct": savings_pct,
        "estimated_usd_before": usd_before,
        "estimated_usd_after": usd_after,
        "usd_saved_per_run": usd_saved,
        "measurement_method": measurement_method,
        "warnings": warnings,
    }


def validate_output_schema(tool_context: ToolContext) -> dict:
    """Verify optimized query preserves final output column names and types.

    Reads baseline and optimized_measurement.output_schema from session state.

    Returns:
        dict with passed flag and any mismatches.
    """
    original = _schema_fields(tool_context.state.get("baseline"))
    optimized = _schema_fields(tool_context.state.get("optimized_measurement"))
    result = dump_model(SchemaCheck, _compare_column_schemas(original, optimized))
    tool_context.state["schema_check"] = result
    return result


def compare_query_stats(tool_context: ToolContext) -> dict:
    """Compare bytes processed and estimate on-demand savings.

    Reads baseline and optimized_measurement from session state.

    Returns:
        dict with bytes delta, savings percentage, and USD estimates.
    """
    before = tool_context.state.get("baseline") or {}
    after = tool_context.state.get("optimized_measurement") or {}
    if not isinstance(before, dict):
        before = {}
    if not isinstance(after, dict):
        after = {}
    result = dump_model(CompareResult, _compare_measurements(before, after))
    tool_context.state["compare"] = result
    return result


def diff_sql(
    after: str,
    tool_context: ToolContext | None = None,
    before: str | None = None,
) -> dict:
    """Produce a unified diff between original and optimized SQL.

    Args:
        after: Optimized SQL.
        before: Original SQL. Defaults to original_sql in session state.

    Returns:
        dict with unified diff text.
    """
    original = before
    if original is None and tool_context is not None:
        original = tool_context.state.get("original_sql")
    original = original or ""
    diff = difflib.unified_diff(
        original.splitlines(),
        after.splitlines(),
        fromfile="original.sql",
        tofile="optimized.sql",
        lineterm="",
    )
    return {"status": "success", "diff": "\n".join(diff)}
