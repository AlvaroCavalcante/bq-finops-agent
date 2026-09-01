"""Workflow helpers: seed session state and the optimize/validate loop gate."""

from __future__ import annotations

import json
import re
from typing import Any

from google.adk.agents.context import Context
from google.adk.events.event import Event
from google.adk.events.event_actions import EventActions
from pydantic import ValidationError

from app.schemas import PipelineInput
from app.tools.safety import is_readonly_select

MAX_OPTIMIZATION_ITERATIONS = 3
CONTINUE_ROUTE = "continue"
EXIT_ROUTE = "exit"

_SQL_FENCE = re.compile(r"```(?:sql)?\s*\n(.*?)```", re.IGNORECASE | re.DOTALL)
_SQL_START = re.compile(r"\b(WITH|SELECT)\b", re.IGNORECASE)


def extract_sql(text: str) -> str:
    """Pull SQL from a fenced block or from the first SELECT/WITH onward."""
    stripped = text.strip()
    fences = _SQL_FENCE.findall(stripped)
    if fences:
        return fences[0].strip()
    match = _SQL_START.search(stripped)
    if match:
        return stripped[match.start() :].strip()
    return stripped


def _content_text(node_input: Any) -> str:
    if node_input is None:
        return ""
    if isinstance(node_input, str):
        return node_input
    parts = getattr(node_input, "parts", None)
    if parts is not None:
        return "".join(getattr(part, "text", "") or "" for part in parts)
    return str(node_input)


def _as_pipeline_input(node_input: Any) -> PipelineInput:
    if isinstance(node_input, PipelineInput):
        return node_input
    if isinstance(node_input, dict) and "sql" in node_input:
        return PipelineInput.model_validate(node_input)
    text = _content_text(node_input).strip()
    if text.startswith("{"):
        try:
            return PipelineInput.model_validate_json(text)
        except ValidationError:
            try:
                return PipelineInput.model_validate(json.loads(text))
            except (ValidationError, json.JSONDecodeError):
                pass
    return PipelineInput(sql=extract_sql(text), context=None)


def seed_pipeline_context(node_input: Any) -> Event:
    """Persist PipelineInput on session state for later nodes and tools."""
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
