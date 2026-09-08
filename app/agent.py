"""One-shot BigQuery SQL optimizer workflow."""

from google.adk.agents import Agent
from google.adk.apps import App
from google.adk.models import Gemini
from google.adk.workflow import Workflow
from google.genai import types

from app.callbacks import after_tool_callback
from app.prompts import load_prompt
from app.quiet_ui import QuietUiPlugin
from app.schemas import (
    MetadataStep,
    OptimizationReport,
    OptimizerStep,
    PipelineInput,
    ValidationStep,
)
from app.tools.antipatterns import detect_sql_antipatterns
from app.tools.compare import compare_query_stats, diff_sql, validate_output_schema
from app.tools.measure_cost import list_referenced_tables, measure_query_cost
from app.tools.metadata import (
    get_partition_summary,
    get_table_metadata,
    get_view_definition,
)
from app.tools.pipeline import (
    DEFAULT_ROUTE,
    OPTIMIZE_ROUTE,
    decide_optimization_loop,
    seed_pipeline_context,
)
from app.tools.report import render_report

DEFAULT_MODEL = "gemini-3.6-flash"
_INCLUDE_CONTENTS = "default"


def _model(model_name: str = DEFAULT_MODEL) -> Gemini:
    return Gemini(
        model=model_name,
        retry_options=types.HttpRetryOptions(attempts=3),
    )


input_agent = Agent(
    name="input_agent",
    model=_model("gemini-3.5-flash-lite"),
    instruction=load_prompt("input"),
    description="Extracts SQL and optional context from the user message.",
    output_schema=PipelineInput,
    output_key="pipeline_input",
    include_contents=_INCLUDE_CONTENTS,
)

metadata_agent = Agent(
    name="metadata_agent",
    model=_model(),
    instruction=load_prompt("metadata"),
    description=(
        "Collects table metadata and baseline query bytes."
    ),
    output_schema=MetadataStep,
    tools=[
        list_referenced_tables,
        get_table_metadata,
        get_partition_summary,
        get_view_definition,
        measure_query_cost,
    ],
    after_tool_callback=after_tool_callback,
    include_contents=_INCLUDE_CONTENTS,
    output_key="metadata_step",
)

optimizer_agent = Agent(
    name="optimizer_agent",
    model=_model(),  # gemini-3.1-pro-preview
    instruction=load_prompt("optimizer"),
    description="Rewrites BigQuery SQL to reduce bytes processed and query costs.",
    output_schema=OptimizerStep,
    tools=[detect_sql_antipatterns, diff_sql],
    after_tool_callback=after_tool_callback,
    include_contents=_INCLUDE_CONTENTS,
    output_key="optimizer_step",
)

validator_agent = Agent(
    name="validator_agent",
    model=_model(),
    instruction=load_prompt("validator"),
    description="Validates optimized SQL and compares bytes.",
    output_schema=ValidationStep,
    tools=[measure_query_cost, validate_output_schema, compare_query_stats],
    after_tool_callback=after_tool_callback,
    include_contents=_INCLUDE_CONTENTS,
    output_key="validation",
)

root_agent = Workflow(
    name="bq_finops_agent",
    description=(
        "One-shot BigQuery SQL optimizer: metadata, "
        "optimize/validate loop, then a structured report."
    ),
    output_schema=OptimizationReport,
    edges=[
        ("START", input_agent),
        (input_agent, seed_pipeline_context),
        (seed_pipeline_context, metadata_agent),
        (metadata_agent, optimizer_agent),
        (optimizer_agent, validator_agent),
        (validator_agent, decide_optimization_loop),
        (
            decide_optimization_loop,
            {
                OPTIMIZE_ROUTE: optimizer_agent,
                DEFAULT_ROUTE: render_report,
            },
        ),
    ],
)

app = App(
    root_agent=root_agent,
    name="app",
    plugins=[QuietUiPlugin()],
)

__all__ = [
    "app",
    "input_agent",
    "metadata_agent",
    "optimizer_agent",
    "render_report",
    "root_agent",
    "validator_agent",
]
