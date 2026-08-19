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

from google.adk.agents import LoopAgent, SequentialAgent

from app.agent import root_agent


def test_root_agent_is_sequential_pipeline() -> None:
    assert isinstance(root_agent, SequentialAgent)
    assert root_agent.name == "bq_finops_agent"
    assert len(root_agent.sub_agents) == 4
    assert root_agent.sub_agents[0].name == "baseline_agent"
    assert root_agent.sub_agents[1].name == "metadata_agent"
    assert isinstance(root_agent.sub_agents[2], LoopAgent)
    assert root_agent.sub_agents[3].name == "formatter_agent"
