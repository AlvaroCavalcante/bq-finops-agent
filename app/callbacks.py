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
