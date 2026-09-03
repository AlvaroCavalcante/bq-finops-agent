from __future__ import annotations

import difflib
from typing import Any

from pydantic import BaseModel

from app.schemas import ColumnField, CompareResult, SchemaCheck, dump_model
from app.tools.bq_client import get_usd_per_tib


def _as_column(item: Any) -> dict | None:
    if isinstance(item, dict):
        return item
    if isinstance(item, BaseModel):
        return item.model_dump()
    return None


def _compare_column_schemas(original: list, optimized: list) -> dict:
    mismatches: list[str] = []
    if len(original) != len(optimized):
        mismatches.append(
            f"Column count differs: {len(original)} vs {len(optimized)}."
        )

    for i, (o, n) in enumerate(zip(original, optimized, strict=False)):
        original_col = _as_column(o)
        optimized_col = _as_column(n)
        if original_col is None or optimized_col is None:
            mismatches.append(
                f"Column {i}: expected {{name, type}} objects, "
                f"got {type(o).__name__} vs {type(n).__name__}."
            )
            continue
        if original_col.get("name") != optimized_col.get("name"):
            mismatches.append(
                f"Column {i}: name '{original_col.get('name')}' vs "
                f"'{optimized_col.get('name')}'."
            )
        if original_col.get("type") != optimized_col.get("type"):
            mismatches.append(
                f"Column {i}: type '{original_col.get('type')}' vs "
                f"'{optimized_col.get('type')}'."
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


def validate_output_schema(
    original_schema: list[ColumnField],
    optimized_schema: list[ColumnField],
) -> dict:
    """Verify the optimized query preserves original output column names and types.

    Args:
        original_schema: Copy baseline.output_schema. Each item is a column
            object with name, type, and optional mode. Never pass integers,
            column indices, or a length/shape array.
        optimized_schema: Copy output_schema from the measure_query_cost
            result for the rewritten SQL (same item shape as original_schema).
            optimizer_step has no schema; do not use best_measurement.

    Returns:
        dict with passed flag and any mismatches.
    """
    return dump_model(
        SchemaCheck,
        _compare_column_schemas(original_schema or [], optimized_schema or []),
    )


def compare_query_stats(
    baseline_bytes: int,
    optimized_bytes: int,
    baseline_method: str = "dry_run",
    optimized_method: str = "dry_run",
) -> dict:
    """Compare bytes processed and estimate on-demand savings.

    Args:
        baseline_bytes: total_bytes_processed from the baseline measurement.
        optimized_bytes: total_bytes_processed from the optimized measurement.
        baseline_method: measurement_method of the baseline (dry_run, live, mixed).
        optimized_method: measurement_method of the optimized run.

    Returns:
        dict with bytes delta, savings percentage, and USD estimates.
    """
    return dump_model(
        CompareResult,
        _compare_measurements(
            {
                "total_bytes_processed": baseline_bytes,
                "measurement_method": baseline_method,
            },
            {
                "total_bytes_processed": optimized_bytes,
                "measurement_method": optimized_method,
            },
        ),
    )


def diff_sql(before: str, after: str) -> dict:
    """Produce a unified diff between original and optimized SQL.

    Args:
        before: Original SQL. Pass original_sql from the pipeline context.
        after: Rewritten SQL from this optimization step.

    Returns:
        dict with unified diff text.
    """
    diff = difflib.unified_diff(
        (before or "").splitlines(),
        (after or "").splitlines(),
        fromfile="original.sql",
        tofile="optimized.sql",
        lineterm="",
    )
    return {"status": "success", "diff": "\n".join(diff)}
