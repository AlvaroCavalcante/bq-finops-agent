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

import pytest
from google.adk.events.event import Event
from google.adk.events.event_actions import EventActions
from google.genai import types

from app.quiet_ui import (
    REPORT_HEADING,
    STAGE_MESSAGES,
    QuietUiPlugin,
    debug_enabled,
    is_final_report,
    is_structural,
    project_event,
)


def _text_event(author: str, text: str, **kwargs) -> Event:
    return Event(
        author=author,
        content=types.Content(
            role="model",
            parts=[types.Part(text=text)],
        ),
        **kwargs,
    )


def _tool_event(author: str, name: str) -> Event:
    return Event(
        author=author,
        content=types.Content(
            role="model",
            parts=[
                types.Part(
                    function_call=types.FunctionCall(name=name, args={})
                )
            ],
        ),
    )


def _state_event(author: str) -> Event:
    return Event(
        author=author,
        actions=EventActions(
            state_delta={
                "referenced_tables": ["p.d.t"],
                "discovery_dry_run": {"status": "success"},
            }
        ),
    )


def _text(event: Event | None) -> str:
    if event is None or event.content is None or not event.content.parts:
        return ""
    return "".join(part.text or "" for part in event.content.parts)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("", False),
        ("false", False),
        ("0", False),
        ("true", True),
        ("TRUE", True),
        ("1", True),
        ("yes", True),
    ],
)
def test_debug_enabled(raw: str, expected: bool) -> None:
    assert debug_enabled({"FINOPS_DEBUG": raw}) is expected


@pytest.mark.asyncio
async def test_plugin_passthrough_when_debug_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FINOPS_DEBUG", "true")
    plugin = QuietUiPlugin()
    event = _tool_event("metadata_agent", "get_table_metadata")
    assert await plugin.on_event_callback(
        invocation_context=None, event=event
    ) is None


def test_tool_and_state_events_pass_through() -> None:
    tool, stage = project_event(
        _tool_event("metadata_agent", "get_table_metadata"), None
    )
    assert tool is None
    assert stage == "metadata_agent"
    assert is_structural(_tool_event("metadata_agent", "get_table_metadata"))

    state, stage = project_event(_state_event("metadata_agent"), stage)
    assert state is None
    assert stage == "metadata_agent"
    assert is_structural(_state_event("metadata_agent"))


def test_ten_tool_calls_do_not_become_empty_state_events() -> None:
    last_stage = None
    for _ in range(10):
        replacement, last_stage = project_event(
            _tool_event("metadata_agent", "get_table_metadata"), last_stage
        )
        assert replacement is None
    assert last_stage == "metadata_agent"


def test_text_json_becomes_one_status_line() -> None:
    replacement, last_stage = project_event(
        _text_event("metadata_agent", '{"table_refs": []}'), None
    )
    assert _text(replacement) == STAGE_MESSAGES["metadata_agent"]
    assert last_stage == "metadata_agent"

    duplicate, last_stage = project_event(
        _text_event("metadata_agent", '{"table_refs": ["x"]}'), last_stage
    )
    assert duplicate is None


def test_workflow_output_is_kept_when_rewriting_text() -> None:
    event = _text_event(
        "metadata_agent",
        '{"table_refs": []}',
        output={"table_refs": []},
    )
    replacement, _ = project_event(event, None)
    assert _text(replacement) == STAGE_MESSAGES["metadata_agent"]
    assert replacement is not None
    assert replacement.output == {"table_refs": []}
    assert not is_structural(event)


def test_render_report_markdown_is_unchanged() -> None:
    report = _text_event(
        "render_report",
        f"{REPORT_HEADING}\n\n**12% fewer bytes processed**",
    )
    replacement, last_stage = project_event(report, "validator_agent")
    assert replacement is None
    assert last_stage == "validator_agent"
    assert is_final_report(report)


def test_markdown_heading_passthrough_regardless_of_author() -> None:
    report = _text_event("bq_finops_agent", f"{REPORT_HEADING}\n")
    replacement, _ = project_event(report, "optimizer_agent")
    assert replacement is None


def test_second_optimizer_pass_announces_on_text() -> None:
    last_stage = "validator_agent"
    first, last_stage = project_event(
        _text_event("optimizer_agent", '{"optimized_sql":"..."}'), last_stage
    )
    second, last_stage = project_event(
        _tool_event("optimizer_agent", "detect_sql_antipatterns"), last_stage
    )
    assert _text(first) == STAGE_MESSAGES["optimizer_agent"]
    assert second is None
    assert last_stage == "optimizer_agent"
