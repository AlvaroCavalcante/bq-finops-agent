from google.adk.apps import App

from app.agents.pipeline import root_agent

app = App(
    root_agent=root_agent,
    name="app",
)

__all__ = ["app", "root_agent"]
