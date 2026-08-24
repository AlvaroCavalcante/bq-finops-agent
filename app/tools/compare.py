from __future__ import annotations

import difflib
import json

from app.tools.bq_client import get_usd_per_tib


def _normalize_schema(schema_json: str) -> list[dict]:
    data = json.loads(schema_json)
    if isinstance(data, dict) and "output_schema" in data:
        data = data["output_schema"]
    if isinstance(data, list):
        return data
    return []


def validate_output_schema(original_schema_json: str, optimized_schema_json: str) -> dict:
    """Verify optimized query preserves final output column names and types.

    Args:
        original_schema_json: JSON list of baseline output schema fields.
        optimized_schema_json: JSON list of optimized output schema fields.

    Returns:
        dict with passed flag and any mismatches.
    """
    original = _normalize_schema(original_schema_json)
    optimized = _normalize_schema(optimized_schema_json)

    mismatches: list[str] = []
    if len(original) != len(optimized):
        mismatches.append(
            f"Column count differs: {len(original)} vs {len(optimized)}."
        )

    for i, (o, n) in enumerate(zip(original, optimized, strict=False)):
        if o.get("name") != n.get("name"):
            mismatches.append(
                f"Column {i}: name '{o.get('name')}' vs '{n.get('name')}'."
            )
        if o.get("type") != n.get("type"):
            mismatches.append(
                f"Column {i}: type '{o.get('type')}' vs '{n.get('type')}'."
            )

    passed = len(mismatches) == 0
    return {
        "status": "success",
        "passed": passed,
        "mismatches": mismatches,
    }


def compare_query_stats(before_json: str, after_json: str) -> dict:
    """Compare bytes processed and estimate on-demand savings.

    Args:
        before_json: JSON measurement result for baseline query.
        after_json: JSON measurement result for optimized query.

    Returns:
        dict with bytes delta, savings percentage, and USD estimates.
    """
    before = json.loads(before_json)
    after = json.loads(after_json)

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


def diff_sql(before: str, after: str) -> dict:
    """Produce a unified diff between original and optimized SQL.

    Args:
        before: Original SQL.
        after: Optimized SQL.

    Returns:
        dict with unified diff text.
    """
    diff = difflib.unified_diff(
        before.splitlines(),
        after.splitlines(),
        fromfile="original.sql",
        tofile="optimized.sql",
        lineterm="",
    )
    return {"status": "success", "diff": "\n".join(diff)}
