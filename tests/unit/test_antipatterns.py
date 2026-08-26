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

import json

from app.tools.antipatterns import detect_sql_antipatterns


def test_detect_select_star() -> None:
    result = detect_sql_antipatterns("SELECT * FROM t", "{}")
    patterns = {f["pattern"] for f in result["findings"]}
    assert "select_star" in patterns


def test_detect_key_constraints_available_on_join() -> None:
    meta = json.dumps(
        {
            "p.d.orders": {
                "status": "success",
                "dataset": "d",
                "table": "orders",
                "primary_key_columns": ["order_id"],
                "foreign_keys": [
                    {
                        "name": "fk_customer",
                        "referenced_table": "p.d.customers",
                        "column_references": [
                            {
                                "referencing_column": "customer_id",
                                "referenced_column": "id",
                            }
                        ],
                    }
                ],
            }
        }
    )
    sql = (
        "SELECT o.order_id FROM orders o "
        "JOIN customers c ON o.customer_id = c.id"
    )
    result = detect_sql_antipatterns(sql, meta)
    patterns = {f["pattern"] for f in result["findings"]}
    assert "key_constraints_available" in patterns


def test_key_constraints_not_flagged_without_join() -> None:
    meta = json.dumps(
        {
            "p.d.orders": {
                "status": "success",
                "dataset": "d",
                "table": "orders",
                "primary_key_columns": ["order_id"],
                "foreign_keys": [],
            }
        }
    )
    result = detect_sql_antipatterns("SELECT order_id FROM orders", meta)
    patterns = {f["pattern"] for f in result["findings"]}
    assert "key_constraints_available" not in patterns
