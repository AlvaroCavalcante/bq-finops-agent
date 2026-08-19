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

from google.adk.tools import BaseTool, ToolContext

from app.tools.safety import is_readonly_select


def before_tool_callback(
    tool: BaseTool,
    args: dict,
    tool_context: ToolContext,
) -> dict | None:
    """Block non-readonly SQL before any BigQuery tool runs."""
    sql_keys = ("sql", "query", "before", "after")
    for key in sql_keys:
        if key in args and isinstance(args[key], str):
            ok, err = is_readonly_select(args[key])
            if not ok:
                return {"status": "error", "error_message": err}
    return None
