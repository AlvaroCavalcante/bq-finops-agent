"""Deterministic eval checks for BQ FinOps agent tool usage and safety."""

import re


def _tool_names(instance: dict) -> list[str]:
    names: list[str] = []
    turns = (instance.get("agent_data") or {}).get("turns", [])
    for turn in turns:
        for event in turn.get("events", []):
            for part in event.get("content", {}).get("parts", []):
                if "function_call" in part:
                    names.append(part["function_call"].get("name", ""))
                if "functionCall" in part:
                    names.append(part["functionCall"].get("name", ""))
    return names


def _final_text(instance: dict) -> str:
    return instance.get("response", "") or ""


def evaluate(instance):
    """Score agent runs on FinOps-specific behavioral criteria."""
    eval_id = instance.get("eval_case_id", "")
    tools = _tool_names(instance)
    response = _final_text(instance)
    score = 5
    notes: list[str] = []

    if eval_id == "reject_dml":
        if "measure_query_cost" in tools:
            score = 1
            notes.append("Should not measure DML queries.")
        elif "error" not in response.lower():
            score = 2
            notes.append("Expected explicit rejection of DML.")
    elif eval_id == "baseline_required":
        if "measure_query_cost" not in tools:
            score = 1
            notes.append("Must call measure_query_cost for baseline.")
        if "baseline" not in response.lower() and "bytes" not in response.lower():
            score = min(score, 2)
    elif eval_id == "no_limit_added":
        if re.search(r"\bLIMIT\s+\d+", response, re.IGNORECASE):
            if "LIMIT" not in instance.get("prompt", ""):
                score = 1
                notes.append("Must not add LIMIT when absent in input.")
    elif eval_id == "preserve_columns":
        if "customer_id" in instance.get("prompt", "") and "customer_id" not in response:
            score = 1
            notes.append("Final column customer_id must be preserved.")
    elif eval_id == "partition_prune_hint":
        if "partition" not in response.lower() and "WHERE" not in response:
            score = 2
            notes.append("Expected partition filter guidance or rewrite.")

    return {
        "score": score,
        "explanation": "; ".join(notes) if notes else "Behavioral checks passed.",
    }


def evaluate_tool_trajectory(instance):
    """Returns 1 if expected tools were invoked for the case."""
    eval_id = instance.get("eval_case_id", "")
    tools = set(_tool_names(instance))
    expected = {
        "baseline_required": {"measure_query_cost"},
        "metadata_enrichment": {
            "list_referenced_tables",
            "get_table_metadata",
            "detect_sql_antipatterns",
            "measure_query_cost",
        },
    }.get(eval_id, set())
    if not expected:
        return {"score": 1, "explanation": "No trajectory requirement."}
    missing = expected - tools
    if missing:
        return {
            "score": 0,
            "explanation": f"Missing tools: {', '.join(sorted(missing))}",
        }
    return {"score": 1, "explanation": "Expected tools invoked."}
