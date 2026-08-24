from types import SimpleNamespace

from app.agents.checkers import (
    CONTINUE_ROUTE,
    EXIT_ROUTE,
    MAX_OPTIMIZATION_ITERATIONS,
    decide_optimization_loop,
)


def _ctx(**state):
    return SimpleNamespace(state=state)


def test_exits_when_validation_passes() -> None:
    event = decide_optimization_loop(_ctx(validation={"passed": True}))
    assert event.actions.route == EXIT_ROUTE
    assert event.actions.state_delta["optimization_iteration"] == 1


def test_continues_when_validation_fails_and_retries_remain() -> None:
    event = decide_optimization_loop(_ctx(validation={"passed": False}))
    assert event.actions.route == CONTINUE_ROUTE
    assert event.actions.state_delta["optimization_iteration"] == 1


def test_exits_at_max_iterations_even_if_validation_fails() -> None:
    event = decide_optimization_loop(
        _ctx(
            validation={"passed": False},
            optimization_iteration=MAX_OPTIMIZATION_ITERATIONS - 1,
        )
    )
    assert event.actions.route == EXIT_ROUTE
    assert (
        event.actions.state_delta["optimization_iteration"]
        == MAX_OPTIMIZATION_ITERATIONS
    )


def test_parses_validation_json_string() -> None:
    event = decide_optimization_loop(_ctx(validation='{"passed": true}'))
    assert event.actions.route == EXIT_ROUTE


def test_invalid_validation_payload_retries() -> None:
    event = decide_optimization_loop(_ctx(validation="not-json"))
    assert event.actions.route == CONTINUE_ROUTE
