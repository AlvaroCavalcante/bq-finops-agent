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

from google.adk.agents import Agent, LoopAgent, SequentialAgent
from google.adk.models import Gemini
from google.genai import types

from app.agents.checkers import ValidationEscalationChecker
from app.callbacks import before_tool_callback
from app.prompts import load_prompt
from app.schemas import OptimizationReport
from app.tools.antipatterns import detect_sql_antipatterns
from app.tools.compare import compare_query_stats, diff_sql, validate_output_schema
from app.tools.measure_cost import measure_query_cost
from app.tools.metadata import (
    get_partition_summary,
    get_table_metadata,
    get_view_definition,
)
from app.tools.safety import assert_readonly_select

MODEL = "gemini-3.6-flash"


def _model() -> Gemini:
    return Gemini(
        model=MODEL,
        retry_options=types.HttpRetryOptions(attempts=3),
    )


def _gen_config() -> types.GenerateContentConfig:
    return types.GenerateContentConfig(temperature=0.1, max_output_tokens=8192)


baseline_agent = Agent(
    name="baseline_agent",
    model=_model(),
    instruction=load_prompt("baseline"),
    description="Measures baseline bytes for the input SQL.",
    tools=[measure_query_cost, assert_readonly_select],
    before_tool_callback=before_tool_callback,
    generate_content_config=_gen_config(),
    output_key="baseline_step",
)

metadata_agent = Agent(
    name="metadata_agent",
    model=_model(),
    instruction=load_prompt("metadata"),
    description="Collects table metadata and SQL anti-patterns.",
    tools=[
        get_table_metadata,
        get_partition_summary,
        get_view_definition,
        detect_sql_antipatterns,
    ],
    before_tool_callback=before_tool_callback,
    generate_content_config=_gen_config(),
    output_key="metadata_step",
)

optimizer_agent = Agent(
    name="optimizer_agent",
    model=_model(),
    instruction=load_prompt("optimizer"),
    description="Rewrites SQL to reduce bytes processed.",
    tools=[diff_sql],
    generate_content_config=_gen_config(),
    output_key="optimizer_step",
)

validator_agent = Agent(
    name="validator_agent",
    model=_model(),
    instruction=load_prompt("validator"),
    description="Validates optimized SQL and compares bytes.",
    tools=[measure_query_cost, validate_output_schema, compare_query_stats],
    before_tool_callback=before_tool_callback,
    generate_content_config=_gen_config(),
    output_key="validation",
)

validation_checker = ValidationEscalationChecker(name="validation_checker")

optimization_loop = LoopAgent(
    name="optimization_loop",
    sub_agents=[optimizer_agent, validator_agent, validation_checker],
    max_iterations=3,
)

formatter_agent = Agent(
    name="formatter_agent",
    model=_model(),
    instruction=load_prompt("formatter"),
    description="Formats the final optimization report.",
    output_schema=OptimizationReport,
    output_key="optimization_report",
    generate_content_config=_gen_config(),
)

root_agent = SequentialAgent(
    name="bq_finops_agent",
    sub_agents=[
        baseline_agent,
        metadata_agent,
        optimization_loop,
        formatter_agent,
    ],
)
