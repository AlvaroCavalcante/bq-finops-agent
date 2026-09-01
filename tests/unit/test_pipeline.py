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

from google.genai import types

from app.schemas import PipelineInput
from app.tools.pipeline import (
    CONTINUE_ROUTE,
    EXIT_ROUTE,
    MAX_OPTIMIZATION_ITERATIONS,
    decide_optimization_loop,
    extract_sql,
    seed_pipeline_context,
)


def _ctx(**state):
    return SimpleNamespace(state=state)


def _passing_validation_state(**extra):
    state = {
        "schema_check": {"status": "success", "passed": True, "mismatches": []},
        "compare": {
            "status": "success",
            "baseline_bytes": 100,
            "optimized_bytes": 40,
            "bytes_saved": 60,
            "savings_pct": 60.0,
            "warnings": [],
        },
        "baseline": {"status": "success", "total_bytes_processed": 100},
        "optimized_measurement": {
            "status": "success",
            "total_bytes_processed": 40,
        },
        "baseline_measured": True,
        "optimized_verified_live": True,
        "validation": {"retry_hint": None, "warnings": []},
    }
    state.update(extra)
    return state


def test_extract_sql_from_fenced_block() -> None:
    text = "Please optimize:\n```sql\nSELECT 1\n```\nthanks"
    assert extract_sql(text) == "SELECT 1"


def test_extract_sql_from_leading_prose() -> None:
    text = "Otimize a seguinte query:\n\nSELECT * FROM t WHERE x = 1"
    assert extract_sql(text).startswith("SELECT")


def test_seed_pipeline_context_json() -> None:
    event = seed_pipeline_context('{"sql": "SELECT 1", "context": "nightly"}')
    assert event.output["sql"] == "SELECT 1"
    assert event.actions.state_delta["original_sql"] == "SELECT 1"
    assert event.actions.state_delta["user_context"] == "nightly"


def test_seed_pipeline_context_dict() -> None:
    event = seed_pipeline_context({"sql": "SELECT 2"})
    assert event.output["sql"] == "SELECT 2"


def test_seed_pipeline_context_persists_original_sql() -> None:
    event = seed_pipeline_context("Otimize:\nSELECT 1 FROM t")
    assert event.output["sql"].startswith("SELECT")
    assert event.actions.state_delta["original_sql"].startswith("SELECT")
    assert "input_error" not in event.actions.state_delta


def test_seed_pipeline_context_rejects_dml() -> None:
    event = seed_pipeline_context("DELETE FROM t WHERE 1=1")
    assert event.actions.state_delta["input_error"]
    assert event.output["sql"].upper().startswith("DELETE")


def test_seed_pipeline_context_from_content() -> None:
    content = types.Content(
        role="user",
        parts=[types.Part.from_text(text="WITH x AS (SELECT 1) SELECT * FROM x")],
    )
    event = seed_pipeline_context(content)
    assert event.output["sql"].upper().startswith("WITH")
    parsed = PipelineInput.model_validate(event.output)
    assert parsed.sql.upper().startswith("WITH")


def test_exits_when_validation_checks_pass() -> None:
    event = decide_optimization_loop(_ctx(**_passing_validation_state()))
    assert event.actions.route == EXIT_ROUTE
    assert event.actions.state_delta["optimization_iteration"] == 1
    assert event.actions.state_delta["validation"]["passed"] is True


def test_continues_when_checks_fail_and_retries_remain() -> None:
    event = decide_optimization_loop(_ctx())
    assert event.actions.route == CONTINUE_ROUTE
    assert event.output["passed"] is False
    assert event.output["retry_hint"]
    assert event.actions.state_delta["optimization_iteration"] == 1


def test_exits_at_max_iterations_even_if_checks_fail() -> None:
    event = decide_optimization_loop(
        _ctx(optimization_iteration=MAX_OPTIMIZATION_ITERATIONS - 1)
    )
    assert event.actions.route == EXIT_ROUTE
    assert (
        event.actions.state_delta["optimization_iteration"]
        == MAX_OPTIMIZATION_ITERATIONS
    )


def test_llm_passed_flag_is_not_trusted() -> None:
    event = decide_optimization_loop(
        _ctx(validation={"passed": True, "retry_hint": None})
    )
    assert event.actions.route == CONTINUE_ROUTE
    assert event.output["passed"] is False
