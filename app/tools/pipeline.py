"""Workflow helpers: seed session state and the optimize/validate loop gate."""

from __future__ import annotations

from typing import Any

from google.adk.agents.context import Context
from google.adk.events.event import Event

from app.schemas import PipelineInput
from app.tools.safety import is_readonly_select

MAX_OPTIMIZATION_ITERATIONS = 3
MIN_IMPROVEMENT_PCT = 5.0
CONTINUE_ROUTE = "continue"
EXIT_ROUTE = "exit"

LOOP_FIX_CORRECTNESS = "fix_correctness"
LOOP_RETRY_NO_SAVINGS = "retry_no_savings"
LOOP_SQUEEZE_MORE = "squeeze_more"
LOOP_PLATEAU = "plateau"
LOOP_MAX_ITERATIONS = "max_iterations"


def _as_pipeline_input(node_input: Any) -> PipelineInput:
    if isinstance(node_input, PipelineInput):
        return node_input
    if isinstance(node_input, str):
        return PipelineInput.model_validate_json(node_input)
    return PipelineInput.model_validate(node_input)


def seed_pipeline_context(node_input: Any) -> Event:
    """Copy PipelineInput onto session state for later nodes and tools."""
    parsed = _as_pipeline_input(node_input)
    ok, err = is_readonly_select(parsed.sql)
    state: dict[str, Any] = {
        "original_sql": parsed.sql,
        "user_context": parsed.context,
        "tables_metadata": {},
        "partition_summaries": {},
        "view_definitions": {},
        "antipatterns": [],
        "referenced_tables": [],
        "baseline": {},
        "validation": {},
        "optimizer_step": {},
    }
    if not ok:
        state["input_error"] = err
    return Event(output=parsed.model_dump(mode="json"), state=state)


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _marginal_savings_pct(previous_bytes: int, current_bytes: int) -> float:
    if previous_bytes <= 0:
        return 0.0
    return round(
        100.0 * (previous_bytes - current_bytes) / previous_bytes, 2
    )


def _current_bytes(ctx: Context) -> int:
    optimized = _as_dict(ctx.state.get("optimized_measurement"))
    if optimized.get("total_bytes_processed") is not None:
        return _as_int(optimized.get("total_bytes_processed"))
    return _as_int(_as_dict(ctx.state.get("compare")).get("optimized_bytes"))


def _previous_bytes(ctx: Context) -> int:
    if ctx.state.get("best_bytes") is not None:
        return _as_int(ctx.state.get("best_bytes"))
    return _as_int(
        _as_dict(ctx.state.get("baseline")).get("total_bytes_processed")
    )


def _compute_validation(ctx: Context) -> dict[str, Any]:
    schema_raw = ctx.state.get("schema_check")
    compare_raw = ctx.state.get("compare")
    llm = ctx.state.get("validation") or {}
    if not isinstance(llm, dict):
        llm = {}

    schema_passed = isinstance(
        schema_raw, dict) and schema_raw.get("passed") is True
    compare_ok = isinstance(compare_raw, dict) and compare_raw.get(
        "status") == "success"
    baseline = ctx.state.get("baseline") or {}
    optimized = ctx.state.get("optimized_measurement") or {}
    measurements_ok = (
        isinstance(baseline, dict)
        and baseline.get("status") == "success"
        and isinstance(optimized, dict)
        and optimized.get("status") == "success"
    )
    passed = schema_passed and compare_ok and measurements_ok

    warnings: list[str] = []
    if isinstance(compare_raw, dict):
        warnings.extend(str(w) for w in (compare_raw.get("warnings") or []))
    warnings.extend(str(w) for w in (llm.get("warnings") or []))

    retry_hint = llm.get("retry_hint")
    if not passed and not retry_hint:
        retry_hint = "Fix schema mismatches and retry the rewrite."

    compare: dict[str, Any]
    if compare_ok:
        compare = compare_raw
    else:
        compare = {
            "status": "error",
            "error_message": "compare_query_stats was not run.",
        }

    schema_check = (
        schema_raw
        if isinstance(schema_raw, dict) and "passed" in schema_raw
        else {
            "status": "success",
            "passed": False,
            "mismatches": ["Schema check was not run."],
        }
    )

    return {
        "passed": passed,
        "schema_check": schema_check,
        "compare": compare,
        "warnings": warnings,
        "retry_hint": None if passed else retry_hint,
        "baseline_measured": bool(ctx.state.get("baseline_measured")),
        "optimized_verified_live": bool(ctx.state.get("optimized_verified_live")),
    }


def _continue_retry_hint(
    loop_reason: str,
    marginal_savings_pct: float,
    compare: dict[str, Any],
) -> str | None:
    if loop_reason == LOOP_RETRY_NO_SAVINGS:
        return (
            "Rewrite did not reduce bytes by at least "
            f"{MIN_IMPROVEMENT_PCT:g}% versus the baseline "
            f"(marginal {marginal_savings_pct}%). Try different techniques "
            "starting from original_sql. Do not keep a rewrite that is equal "
            "or worse."
        )
    if loop_reason == LOOP_SQUEEZE_MORE:
        savings_vs_original = compare.get("savings_pct") or 0
        return (
            f"Bytes reduced by {savings_vs_original}% vs original "
            f"({marginal_savings_pct}% vs last accepted). Continue from the "
            "rewritten SQL in optimizer_step to find additional savings. "
            "Do not change the output schema."
        )
    return None


def _original_report_state(
    ctx: Context, validation: dict[str, Any]
) -> dict[str, Any]:
    original = str(ctx.state.get("original_sql") or "")
    baseline = _as_dict(ctx.state.get("baseline"))
    compare = _as_dict(validation.get("compare"))
    bytes_ = _as_int(
        baseline.get("total_bytes_processed"),
        _as_int(compare.get("baseline_bytes")),
    )
    usd = float(compare.get("estimated_usd_before") or 0.0)
    method = (
        baseline.get("measurement_method")
        or compare.get("measurement_method")
        or "dry_run"
    )
    restored_compare = {
        "status": "success",
        "baseline_bytes": bytes_,
        "optimized_bytes": bytes_,
        "bytes_saved": 0,
        "savings_pct": 0.0,
        "estimated_usd_before": usd,
        "estimated_usd_after": usd,
        "usd_saved_per_run": 0.0,
        "measurement_method": method,
        "warnings": list(compare.get("warnings") or []),
    }
    return {
        "optimizer_step": {
            "optimized_sql": original,
            "changes_applied": [],
            "recommendations_not_applied": [],
        },
        "compare": restored_compare,
        "optimized_measurement": baseline,
    }


def decide_optimization_loop(ctx: Context) -> Event:
    """Hill-climb until a threshold plateau or max optimizer/validator iterations."""
    validation = _compute_validation(ctx)
    iteration = int(ctx.state.get("optimization_iteration", 0)) + 1
    at_max = iteration >= MAX_OPTIMIZATION_ITERATIONS
    passed = bool(validation["passed"])
    previous_bytes = _previous_bytes(ctx)
    current_bytes = _current_bytes(ctx)
    marginal_savings_pct = _marginal_savings_pct(previous_bytes, current_bytes)
    improved = (
        passed
        and previous_bytes > 0
        and marginal_savings_pct >= MIN_IMPROVEMENT_PCT
    )

    accept = False
    if at_max:
        loop_reason = LOOP_MAX_ITERATIONS
        should_exit = True
        accept = improved
    elif not passed:
        loop_reason = LOOP_FIX_CORRECTNESS
        should_exit = False
    elif improved:
        loop_reason = LOOP_SQUEEZE_MORE
        should_exit = False
        accept = True
    elif iteration == 1:
        loop_reason = LOOP_RETRY_NO_SAVINGS
        should_exit = False
    else:
        loop_reason = LOOP_PLATEAU
        should_exit = True

    validation["loop_reason"] = loop_reason
    validation["marginal_savings_pct"] = marginal_savings_pct
    if should_exit:
        validation["retry_hint"] = None
    elif passed:
        validation["retry_hint"] = _continue_retry_hint(
            loop_reason, marginal_savings_pct, _as_dict(validation.get("compare"))
        )

    if loop_reason == LOOP_PLATEAU:
        validation["warnings"] = [
            *list(validation.get("warnings") or []),
            "Further rewrites did not beat the "
            f"{MIN_IMPROVEMENT_PCT:g}% byte-reduction threshold; "
            "keeping the best valid SQL.",
        ]
    elif should_exit and loop_reason == LOOP_MAX_ITERATIONS:
        validation["warnings"] = [
            *list(validation.get("warnings") or []),
            "Stopped at the maximum number of optimization iterations.",
        ]

    optimizer_step = _as_dict(ctx.state.get("optimizer_step"))
    compare = _as_dict(ctx.state.get("compare"))
    measurement = _as_dict(ctx.state.get("optimized_measurement"))
    optimized_sql = str(
        optimizer_step.get("optimized_sql")
        or ctx.state.get("original_sql")
        or ""
    )

    state: dict[str, Any] = {
        "optimization_iteration": iteration,
        "validation": validation,
    }

    if accept:
        state["best_sql"] = optimized_sql
        state["best_bytes"] = current_bytes
        state["best_optimizer_step"] = optimizer_step
        state["best_compare"] = compare
        state["best_measurement"] = measurement

    if should_exit:
        best_step = (
            state.get("best_optimizer_step")
            or ctx.state.get("best_optimizer_step")
        )
        if isinstance(best_step, dict) and best_step.get("optimized_sql"):
            restored_compare = (
                state.get("best_compare")
                or ctx.state.get("best_compare")
                or compare
            )
            restored_measurement = (
                state.get("best_measurement")
                or ctx.state.get("best_measurement")
                or measurement
            )
            state["optimizer_step"] = best_step
            state["compare"] = restored_compare
            state["optimized_measurement"] = restored_measurement
            validation["passed"] = True
            validation["compare"] = restored_compare
            validation["retry_hint"] = None
        else:
            original_report = _original_report_state(ctx, validation)
            state.update(original_report)
            validation["compare"] = original_report["compare"]
            validation["retry_hint"] = None
    elif loop_reason == LOOP_FIX_CORRECTNESS:
        best_step = ctx.state.get("best_optimizer_step")
        if isinstance(best_step, dict) and best_step.get("optimized_sql"):
            state["optimizer_step"] = best_step

    return Event(
        output=validation,
        route=EXIT_ROUTE if should_exit else CONTINUE_ROUTE,
        state=state,
    )
