"""Project workflow events to a short status stream unless FINOPS_DEBUG is on.

ADK persists whatever ``on_event_callback`` returns (same object is appended
to the session and streamed). Replacing tool-call, tool-response, state-delta,
or workflow-output events strips the data later nodes and the model need, and
the playground then shows empty ``{} State: ...`` bubbles in a loop.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from google.adk.events.event import Event
from google.adk.plugins.base_plugin import BasePlugin
from google.genai import types

if TYPE_CHECKING:
    from google.adk.agents.invocation_context import InvocationContext

DEBUG_ENV = "FINOPS_DEBUG"
RENDER_REPORT_AUTHOR = "render_report"
REPORT_HEADING = "# BigQuery optimization report"

STAGE_MESSAGES: dict[str, str] = {
    "input_agent": "Reading your SQL...",
    "seed_pipeline_context": "Checking that the query is read-only...",
    "metadata_agent": (
        "Collecting table metadata and measuring the original query..."
    ),
    "optimizer_agent": "Rewriting the query to reduce bytes processed...",
    "validator_agent": "Validating the rewrite and comparing cost...",
    "decide_optimization_loop": (
        "Checking whether another optimization pass is needed..."
    ),
}


def debug_enabled(environ: dict[str, str] | None = None) -> bool:
    raw = (environ if environ is not None else os.environ).get(DEBUG_ENV, "")
    return str(raw).strip().lower() in ("1", "true", "yes")


def _event_text(event: Event) -> str:
    content = event.content
    if content is None or not content.parts:
        return ""
    return "".join(part.text or "" for part in content.parts)


def is_final_report(event: Event) -> bool:
    if event.author == RENDER_REPORT_AUTHOR:
        return True
    return _event_text(event).lstrip().startswith(REPORT_HEADING)


def is_structural(event: Event) -> bool:
    """Events the runner must persist unchanged for the pipeline to work."""
    if event.get_function_calls() or event.get_function_responses():
        return True
    actions = event.actions
    if actions is not None:
        if actions.state_delta or actions.route:
            return True
    return False


def _status_event(source: Event, message: str) -> Event:
    return source.model_copy(
        update={
            "content": types.Content(
                role="model",
                parts=[types.Part(text=message)],
            ),
        }
    )


def project_event(
    event: Event, last_stage: str | None
) -> tuple[Event | None, str | None]:
    """Replace noisy *text* dumps with one status line per pipeline stage.

    Returns ``(None, last_stage)`` to keep the original event (passthrough).
    """
    stage = (
        event.author if event.author in STAGE_MESSAGES else last_stage
    )

    if event.author == "user" or is_final_report(event):
        return None, last_stage

    if is_structural(event):
        return None, stage

    if event.author not in STAGE_MESSAGES:
        return None, last_stage

    if last_stage == event.author:
        return None, last_stage
    return _status_event(event, STAGE_MESSAGES[event.author]), event.author


class QuietUiPlugin(BasePlugin):
    """Hide sub-agent JSON dumps unless FINOPS_DEBUG is enabled.

    Tool calls, tool responses, and state deltas pass through unchanged.
    """

    def __init__(self, name: str = "quiet_ui") -> None:
        super().__init__(name=name)
        self._last_stage: str | None = None

    async def before_run_callback(
        self, *, invocation_context: InvocationContext
    ) -> None:
        self._last_stage = None
        return None

    async def on_event_callback(
        self, *, invocation_context: InvocationContext, event: Event
    ) -> Event | None:
        if debug_enabled():
            return None
        replacement, self._last_stage = project_event(event, self._last_stage)
        return replacement
