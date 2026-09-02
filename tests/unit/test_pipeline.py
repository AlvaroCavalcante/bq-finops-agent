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

import pytest
from pydantic import ValidationError

from app.schemas import PipelineInput
from app.tools.pipeline import (
    DEFAULT_ROUTE,
    LOOP_FIX_CORRECTNESS,
    LOOP_MAX_ITERATIONS,
    LOOP_PLATEAU,
    LOOP_RETRY_NO_SAVINGS,
    LOOP_SQUEEZE_MORE,
    MAX_OPTIMIZATION_ITERATIONS,
    MIN_IMPROVEMENT_PCT,
    OPTIMIZE_ROUTE,
    decide_optimization_loop,
    seed_pipeline_context,
)

ORIGINAL_SQL = "SELECT * FROM t"
BEST_SQL = "SELECT a FROM t"
SQUEEZE_SQL = "SELECT a FROM t WHERE a IS NOT NULL"


def _ctx(**state):
    return SimpleNamespace(state=state)


def _optimizer_step(sql: str) -> dict:
    return {
        "optimized_sql": sql,
        "changes_applied": [{"category": "projection", "description": "drop *"}],
        "recommendations_not_applied": [],
    }


def _passing_validation_state(**extra):
    state = {
        "original_sql": ORIGINAL_SQL,
        "optimizer_step": _optimizer_step(BEST_SQL),
        "schema_check": {"status": "success", "passed": True, "mismatches": []},
        "compare": {
            "status": "success",
            "baseline_bytes": 100,
            "optimized_bytes": 40,
            "bytes_saved": 60,
            "savings_pct": 60.0,
            "estimated_usd_before": 1.0,
            "estimated_usd_after": 0.4,
            "usd_saved_per_run": 0.6,
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


def _best_snapshot() -> dict:
    return {
        "best_sql": BEST_SQL,
        "best_bytes": 40,
        "best_optimizer_step": _optimizer_step(BEST_SQL),
        "best_compare": {
            "status": "success",
            "baseline_bytes": 100,
            "optimized_bytes": 40,
            "bytes_saved": 60,
            "savings_pct": 60.0,
            "warnings": [],
        },
        "best_measurement": {
            "status": "success",
            "total_bytes_processed": 40,
        },
    }


def test_seed_pipeline_context_from_model() -> None:
    event = seed_pipeline_context(
        PipelineInput(sql="SELECT 1", context="nightly")
    )
    assert event.output["sql"] == "SELECT 1"
    assert event.actions.state_delta["original_sql"] == "SELECT 1"
    assert event.actions.state_delta["user_context"] == "nightly"
    assert "input_error" not in event.actions.state_delta


def test_seed_pipeline_context_from_dict() -> None:
    event = seed_pipeline_context({"sql": "SELECT 2"})
    assert event.output["sql"] == "SELECT 2"
    assert event.actions.state_delta["original_sql"] == "SELECT 2"
    assert event.actions.state_delta["user_context"] is None


def test_seed_pipeline_context_from_json_string() -> None:
    event = seed_pipeline_context('{"sql": "SELECT 3", "context": "batch"}')
    assert event.output["sql"] == "SELECT 3"
    assert event.actions.state_delta["user_context"] == "batch"


def test_seed_pipeline_context_rejects_dml() -> None:
    event = seed_pipeline_context(PipelineInput(sql="DELETE FROM t WHERE 1=1"))
    assert event.actions.state_delta["input_error"]
    assert event.output["sql"].upper().startswith("DELETE")


def test_seed_pipeline_context_rejects_free_text() -> None:
    with pytest.raises(ValidationError):
        seed_pipeline_context("Otimize:\nSELECT 1 FROM t")


def test_first_pass_improvement_continues_to_squeeze() -> None:
    event = decide_optimization_loop(_ctx(**_passing_validation_state()))
    delta = event.actions.state_delta
    assert event.actions.route == OPTIMIZE_ROUTE
    assert delta["optimization_iteration"] == 1
    assert delta["validation"]["passed"] is True
    assert delta["validation"]["loop_reason"] == LOOP_SQUEEZE_MORE
    assert delta["validation"]["marginal_savings_pct"] == 60.0
    assert delta["best_sql"] == BEST_SQL
    assert delta["best_bytes"] == 40
    assert delta["best_optimizer_step"]["optimized_sql"] == BEST_SQL
    hint = delta["validation"]["retry_hint"] or ""
    assert "Continue from the rewritten SQL" in hint


def test_first_pass_no_savings_retries() -> None:
    event = decide_optimization_loop(
        _ctx(
            **_passing_validation_state(
                optimizer_step=_optimizer_step(ORIGINAL_SQL),
                compare={
                    "status": "success",
                    "baseline_bytes": 100,
                    "optimized_bytes": 100,
                    "bytes_saved": 0,
                    "savings_pct": 0.0,
                    "warnings": ["No byte reduction detected."],
                },
                optimized_measurement={
                    "status": "success",
                    "total_bytes_processed": 100,
                },
            )
        )
    )
    delta = event.actions.state_delta
    assert event.actions.route == OPTIMIZE_ROUTE
    assert delta["validation"]["loop_reason"] == LOOP_RETRY_NO_SAVINGS
    assert delta["validation"]["passed"] is True
    assert delta["validation"]["marginal_savings_pct"] == 0.0
    assert "best_sql" not in delta
    hint = delta["validation"]["retry_hint"] or ""
    assert "original_sql" in hint
    assert f"{MIN_IMPROVEMENT_PCT:g}%" in hint


def test_first_pass_worse_bytes_retries() -> None:
    event = decide_optimization_loop(
        _ctx(
            **_passing_validation_state(
                compare={
                    "status": "success",
                    "baseline_bytes": 100,
                    "optimized_bytes": 150,
                    "bytes_saved": 0,
                    "savings_pct": 0.0,
                    "warnings": ["No byte reduction detected."],
                },
                optimized_measurement={
                    "status": "success",
                    "total_bytes_processed": 150,
                },
            )
        )
    )
    delta = event.actions.state_delta
    assert event.actions.route == OPTIMIZE_ROUTE
    assert delta["validation"]["loop_reason"] == LOOP_RETRY_NO_SAVINGS
    assert delta["validation"]["marginal_savings_pct"] == -50.0
    assert "best_sql" not in delta


def test_continues_when_checks_fail_and_retries_remain() -> None:
    event = decide_optimization_loop(_ctx())
    assert event.actions.route == OPTIMIZE_ROUTE
    assert event.output["passed"] is False
    assert event.output["retry_hint"]
    assert event.output["loop_reason"] == LOOP_FIX_CORRECTNESS
    assert event.actions.state_delta["optimization_iteration"] == 1


def test_exits_at_max_iterations_even_if_checks_fail() -> None:
    event = decide_optimization_loop(
        _ctx(
            original_sql=ORIGINAL_SQL,
            optimization_iteration=MAX_OPTIMIZATION_ITERATIONS - 1,
        )
    )
    delta = event.actions.state_delta
    assert event.actions.route == DEFAULT_ROUTE
    assert delta["optimization_iteration"] == MAX_OPTIMIZATION_ITERATIONS
    assert delta["validation"]["loop_reason"] == LOOP_MAX_ITERATIONS
    assert delta["optimizer_step"]["optimized_sql"] == ORIGINAL_SQL
    assert delta["compare"]["bytes_saved"] == 0


def test_llm_passed_flag_is_not_trusted() -> None:
    event = decide_optimization_loop(
        _ctx(validation={"passed": True, "retry_hint": None})
    )
    assert event.actions.route == OPTIMIZE_ROUTE
    assert event.output["passed"] is False
    assert event.output["loop_reason"] == LOOP_FIX_CORRECTNESS


def test_second_pass_below_threshold_exits_plateau_and_restores_best() -> None:
    event = decide_optimization_loop(
        _ctx(
            **_passing_validation_state(
                optimization_iteration=1,
                **_best_snapshot(),
                optimizer_step=_optimizer_step(SQUEEZE_SQL),
                compare={
                    "status": "success",
                    "baseline_bytes": 100,
                    "optimized_bytes": 39,
                    "bytes_saved": 61,
                    "savings_pct": 61.0,
                    "warnings": [],
                },
                optimized_measurement={
                    "status": "success",
                    "total_bytes_processed": 39,
                },
            )
        )
    )
    delta = event.actions.state_delta
    assert event.actions.route == DEFAULT_ROUTE
    assert delta["validation"]["loop_reason"] == LOOP_PLATEAU
    assert delta["validation"]["marginal_savings_pct"] == 2.5
    assert "best_sql" not in delta
    assert delta["optimizer_step"]["optimized_sql"] == BEST_SQL
    assert delta["compare"]["optimized_bytes"] == 40
    assert any("5%" in w for w in delta["validation"]["warnings"])


def test_second_pass_above_threshold_continues_and_updates_best() -> None:
    event = decide_optimization_loop(
        _ctx(
            **_passing_validation_state(
                optimization_iteration=1,
                **_best_snapshot(),
                optimizer_step=_optimizer_step(SQUEEZE_SQL),
                compare={
                    "status": "success",
                    "baseline_bytes": 100,
                    "optimized_bytes": 30,
                    "bytes_saved": 70,
                    "savings_pct": 70.0,
                    "warnings": [],
                },
                optimized_measurement={
                    "status": "success",
                    "total_bytes_processed": 30,
                },
            )
        )
    )
    delta = event.actions.state_delta
    assert event.actions.route == OPTIMIZE_ROUTE
    assert delta["validation"]["loop_reason"] == LOOP_SQUEEZE_MORE
    assert delta["validation"]["marginal_savings_pct"] == 25.0
    assert delta["best_sql"] == SQUEEZE_SQL
    assert delta["best_bytes"] == 30
    assert delta["best_optimizer_step"]["optimized_sql"] == SQUEEZE_SQL


def test_second_pass_schema_failure_keeps_best_and_retries() -> None:
    event = decide_optimization_loop(
        _ctx(
            **_passing_validation_state(
                optimization_iteration=1,
                **_best_snapshot(),
                schema_check={
                    "status": "success",
                    "passed": False,
                    "mismatches": ["Column count differs: 1 vs 2."],
                },
                optimizer_step=_optimizer_step(SQUEEZE_SQL),
            )
        )
    )
    delta = event.actions.state_delta
    assert event.actions.route == OPTIMIZE_ROUTE
    assert delta["validation"]["passed"] is False
    assert delta["validation"]["loop_reason"] == LOOP_FIX_CORRECTNESS
    assert "best_sql" not in delta
    assert delta["optimizer_step"]["optimized_sql"] == BEST_SQL


def test_max_iterations_worse_squeeze_restores_best() -> None:
    event = decide_optimization_loop(
        _ctx(
            **_passing_validation_state(
                optimization_iteration=MAX_OPTIMIZATION_ITERATIONS - 1,
                **_best_snapshot(),
                optimizer_step=_optimizer_step(SQUEEZE_SQL),
                compare={
                    "status": "success",
                    "baseline_bytes": 100,
                    "optimized_bytes": 50,
                    "bytes_saved": 50,
                    "savings_pct": 50.0,
                    "warnings": [],
                },
                optimized_measurement={
                    "status": "success",
                    "total_bytes_processed": 50,
                },
            )
        )
    )
    delta = event.actions.state_delta
    assert event.actions.route == DEFAULT_ROUTE
    assert delta["validation"]["loop_reason"] == LOOP_MAX_ITERATIONS
    assert delta["validation"]["passed"] is True
    assert delta["validation"]["marginal_savings_pct"] == -25.0
    assert "best_sql" not in delta
    assert delta["optimizer_step"]["optimized_sql"] == BEST_SQL
    assert delta["compare"]["optimized_bytes"] == 40
    assert delta["optimized_measurement"]["total_bytes_processed"] == 40


def test_max_iterations_accepts_final_improvement() -> None:
    event = decide_optimization_loop(
        _ctx(
            **_passing_validation_state(
                optimization_iteration=MAX_OPTIMIZATION_ITERATIONS - 1,
                **_best_snapshot(),
                optimizer_step=_optimizer_step(SQUEEZE_SQL),
                compare={
                    "status": "success",
                    "baseline_bytes": 100,
                    "optimized_bytes": 30,
                    "bytes_saved": 70,
                    "savings_pct": 70.0,
                    "warnings": [],
                },
                optimized_measurement={
                    "status": "success",
                    "total_bytes_processed": 30,
                },
            )
        )
    )
    delta = event.actions.state_delta
    assert event.actions.route == DEFAULT_ROUTE
    assert delta["validation"]["loop_reason"] == LOOP_MAX_ITERATIONS
    assert delta["best_sql"] == SQUEEZE_SQL
    assert delta["best_bytes"] == 30
    assert delta["optimizer_step"]["optimized_sql"] == SQUEEZE_SQL
    assert delta["compare"]["optimized_bytes"] == 30
