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

from google.adk.workflow import Workflow

from app.agent import root_agent
from app.agents.checkers import CONTINUE_ROUTE, EXIT_ROUTE, decide_optimization_loop
from app.agents.pipeline import (
    formatter_agent,
    metadata_agent,
    optimizer_agent,
    validator_agent,
)


def test_root_agent_is_finops_workflow() -> None:
    assert isinstance(root_agent, Workflow)
    assert root_agent.name == "bq_finops_agent"
    assert root_agent.graph is not None

    node_names = {node.name for node in root_agent.graph.nodes}
    assert node_names == {
        "__START__",
        "metadata_agent",
        "optimizer_agent",
        "validator_agent",
        "decide_optimization_loop",
        "formatter_agent",
    }

    routed = {
        (edge.from_node.name, edge.to_node.name, edge.route)
        for edge in root_agent.graph.edges
    }
    assert ("__START__", "metadata_agent", None) in routed
    assert ("metadata_agent", "optimizer_agent", None) in routed
    assert ("optimizer_agent", "validator_agent", None) in routed
    assert (
        "validator_agent",
        "decide_optimization_loop",
        None,
    ) in routed
    assert (
        "decide_optimization_loop",
        "optimizer_agent",
        CONTINUE_ROUTE,
    ) in routed
    assert (
        "decide_optimization_loop",
        "formatter_agent",
        EXIT_ROUTE,
    ) in routed


def test_workflow_nodes_are_pipeline_agents() -> None:
    nodes = {node.name: node for node in root_agent.graph.nodes}
    assert nodes["metadata_agent"].name == metadata_agent.name
    assert nodes["optimizer_agent"].name == optimizer_agent.name
    assert nodes["validator_agent"].name == validator_agent.name
    assert nodes["formatter_agent"].name == formatter_agent.name
    assert nodes["decide_optimization_loop"].name == decide_optimization_loop.__name__


def test_metadata_agent_includes_discovery_tool() -> None:
    names = [
        getattr(tool, "name", None) or getattr(tool, "__name__", "")
        for tool in metadata_agent.tools
    ]
    assert "list_referenced_tables" in names
    assert "measure_query_cost" in names
