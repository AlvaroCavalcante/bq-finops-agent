"""Deterministic routing for the optimize/validate workflow loop."""

from __future__ import annotations

import json
from typing import Any

from google.adk.agents.context import Context
from google.adk.events.event import Event
from google.adk.events.event_actions import EventActions

MAX_OPTIMIZATION_ITERATIONS = 3
CONTINUE_ROUTE = "continue"
EXIT_ROUTE = "exit"


def _parse_validation(raw: Any) -> dict[str, Any]:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return {}
    return raw if isinstance(raw, dict) else {}


def decide_optimization_loop(ctx: Context) -> Event:
    """Retry optimizer/validator until validation passes or max iterations."""
    validation = _parse_validation(ctx.state.get("validation", {}))
    iteration = int(ctx.state.get("optimization_iteration", 0)) + 1
    passed = validation.get("passed") is True
    should_exit = passed or iteration >= MAX_OPTIMIZATION_ITERATIONS
    return Event.model_validate(
        {
            "output": validation,
            "actions": EventActions(
                route=EXIT_ROUTE if should_exit else CONTINUE_ROUTE,
                state_delta={"optimization_iteration": iteration},
            ),
        }
    )
