"""Workflow helpers: seed session state and the optimize/validate loop gate."""

from __future__ import annotations

from typing import Any

from google.adk.agents.context import Context
from google.adk.events.event import Event
from google.adk.events.event_actions import EventActions

from app.schemas import PipelineInput
from app.tools.safety import is_readonly_select

MAX_OPTIMIZATION_ITERATIONS = 3
CONTINUE_ROUTE = "continue"
EXIT_ROUTE = "exit"


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
    return Event.model_validate(
        {
            "output": parsed.model_dump(mode="json"),
            "actions": EventActions(state_delta=state),
        }
    )


def _compute_validation(ctx: Context) -> dict[str, Any]:
    schema_raw = ctx.state.get("schema_check")
    compare_raw = ctx.state.get("compare")
    llm = ctx.state.get("validation") or {}
    if not isinstance(llm, dict):
        llm = {}

    schema_passed = isinstance(schema_raw, dict) and schema_raw.get("passed") is True
    compare_ok = isinstance(compare_raw, dict) and compare_raw.get("status") == "success"
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


def decide_optimization_loop(ctx: Context) -> Event:
    """Retry optimizer/validator until checks pass or max iterations."""
    validation = _compute_validation(ctx)
    iteration = int(ctx.state.get("optimization_iteration", 0)) + 1
    should_exit = validation["passed"] or iteration >= MAX_OPTIMIZATION_ITERATIONS
    return Event.model_validate(
        {
            "output": validation,
            "actions": EventActions(
                route=EXIT_ROUTE if should_exit else CONTINUE_ROUTE,
                state_delta={
                    "optimization_iteration": iteration,
                    "validation": validation,
                },
            ),
        }
    )
