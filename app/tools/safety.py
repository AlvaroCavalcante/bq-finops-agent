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

import re

_FORBIDDEN = re.compile(
    r"\b("
    r"INSERT|UPDATE|DELETE|MERGE|TRUNCATE|DROP|CREATE|ALTER|"
    r"GRANT|REVOKE|EXPORT|LOAD|COPY|EXECUTE|CALL|SCRIPT"
    r")\b",
    re.IGNORECASE,
)


def is_readonly_select(sql: str) -> tuple[bool, str | None]:
    """Return whether SQL is a read-only SELECT/WITH query."""
    stripped = sql.strip().rstrip(";")
    if not stripped:
        return False, "Empty SQL statement."

    if _FORBIDDEN.search(stripped):
        return False, "Only SELECT / WITH … SELECT queries are allowed."

    upper = stripped.upper()
    if not (upper.startswith("SELECT") or upper.startswith("WITH")):
        return False, "Query must start with SELECT or WITH."

    return True, None


def assert_readonly_select(sql: str) -> dict:
    """Validate that SQL is read-only before running against BigQuery.

    Args:
        sql: BigQuery SQL to validate.

    Returns:
        dict with status and optional error message.
    """
    ok, err = is_readonly_select(sql)
    if ok:
        return {"status": "success", "readonly": True}
    return {"status": "error", "readonly": False, "error_message": err}
