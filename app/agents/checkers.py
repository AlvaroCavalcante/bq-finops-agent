from typing import AsyncGenerator

from google.adk.agents import BaseAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event, EventActions


class ValidationEscalationChecker(BaseAgent):
    """Stops the optimization loop when validation passes."""

    async def _run_async_impl(
        self, ctx: InvocationContext
    ) -> AsyncGenerator[Event, None]:
        validation = ctx.session.state.get("validation", {})
        if isinstance(validation, str):
            import json

            try:
                validation = json.loads(validation)
            except json.JSONDecodeError:
                validation = {}

        passed = validation.get("passed") is True
        if passed:
            yield Event(author=self.name, actions=EventActions(escalate=True))
        else:
            yield Event(author=self.name)
