from google.adk.tools.base_tool import BaseTool
from google.adk.tools.tool_context import ToolContext


def _tool_name(tool: BaseTool | None) -> str:
    if tool is None:
        return ""
    return getattr(tool, "name", None) or getattr(tool, "__name__", "") or ""


def _record_keyed(
    tool_context: ToolContext, state_key: str, ref: str, value: dict
) -> None:
    items = dict(tool_context.state.get(state_key) or {})
    items[ref] = value
    tool_context.state[state_key] = items


def _record_table_metadata(result: dict, tool_context: ToolContext) -> None:
    if result.get("status") != "success":
        return
    ref = f"{result['project']}.{result['dataset']}.{result['table']}"
    tables = dict(tool_context.state.get("tables_metadata") or {})
    tables[ref] = result
    tool_context.state["tables_metadata"] = tables
    tool_context.state["has_clustering"] = any(
        bool(meta.get("clustering_fields"))
        for meta in tables.values()
        if meta.get("status") == "success"
    )


def _store_measurement(
    purpose: str, final: dict, tool_context: ToolContext
) -> None:
    if purpose == "baseline":
        err = str(final.get("error_message") or "")
        if final.get("status") == "error" and "get_table_metadata" in err:
            return
        tool_context.state["baseline"] = final
        tool_context.state["baseline_measured"] = final.get(
            "status") == "success"
        return
    if purpose in ("optimized_probe", "optimized_final"):
        tool_context.state["optimized_measurement"] = final
        method = final.get("measurement_method")
        if final.get("status") == "success" and method in ("live", "mixed"):
            tool_context.state["optimized_verified_live"] = True


def after_tool_callback(
    tool: BaseTool,
    args: dict,
    tool_context: ToolContext,
    tool_response: object,
) -> dict | None:
    """Project tool return values into session state."""
    if tool_context is None or not isinstance(tool_response, dict):
        return None
    name = _tool_name(tool)

    if name == "list_referenced_tables":
        if tool_response.get("status") == "success":
            tool_context.state["referenced_tables"] = (
                tool_response.get("referenced_tables") or []
            )
            tool_context.state["discovery_dry_run"] = tool_response
            tool_context.state["discovery_sql"] = args.get("sql")
        return None
    if name == "get_table_metadata":
        _record_table_metadata(tool_response, tool_context)
        return None
    if name == "get_partition_summary":
        ref = f"{args.get('project')}.{args.get('dataset')}.{args.get('table')}"
        _record_keyed(tool_context, "partition_summaries", ref, tool_response)
        return None
    if name == "get_view_definition":
        ref = f"{args.get('project')}.{args.get('dataset')}.{args.get('view')}"
        _record_keyed(tool_context, "view_definitions", ref, tool_response)
        return None
    if name == "measure_query_cost":
        _store_measurement(str(args.get("purpose") or ""),
                           tool_response, tool_context)
        return None
    if name == "validate_output_schema":
        tool_context.state["schema_check"] = tool_response
        return None
    if name == "compare_query_stats":
        tool_context.state["compare"] = tool_response
        return None
    if name == "detect_sql_antipatterns":
        if tool_response.get("status") == "error" and not (
            tool_response.get("findings") or []
        ):
            return None
        tool_context.state["antipatterns"] = tool_response.get(
            "findings") or []
    return None
