"""Final report node: build the OptimizationReport and render it as Markdown."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, TypeVar, cast

from google.adk.agents.context import Context
from google.adk.events.event import Event
from google.adk.events.event_actions import EventActions
from google.genai import types

from app.schemas import (
    ChangeApplied,
    MeasurementMethod,
    OptimizationReport,
    RecommendationNotApplied,
)
from app.tools.safety import forbids_live_execution
from app.tools.utils import as_dict, as_float, as_int, as_str_list, dedupe

BYTE_UNITS = ("B", "KiB", "MiB", "GiB", "TiB", "PiB")
COMPARE_MISSING_WARNING = (
    "Cost comparison was unavailable; byte and USD figures are reported as zero."
)
DML_DRY_RUN_WARNING = (
    "INSERT/MERGE was measured with dry-run only; live execution is disabled "
    "so no data is modified. Clustering savings may be undercounted."
)
_MEASUREMENT_METHODS: tuple[MeasurementMethod, ...] = (
    "dry_run", "live", "mixed")
_MIN_USD = 0.0001

_Entry = TypeVar("_Entry", ChangeApplied, RecommendationNotApplied)


def _entries(value: Any, entry: type[_Entry]) -> list[_Entry]:
    """Coerce optimizer category/description pairs into report entries."""
    entries: list[_Entry] = []
    if not isinstance(value, list):
        return entries
    for item in value:
        fields = as_dict(item)
        category = str(fields.get("category") or "").strip()
        description = str(fields.get("description") or "").strip()
        if not category and not description:
            continue
        entries.append(
            entry(category=category or "other", description=description)
        )
    return entries


def _measurement_method(*candidates: Any) -> MeasurementMethod:
    for candidate in candidates:
        if candidate in _MEASUREMENT_METHODS:
            return cast(MeasurementMethod, candidate)
    return "dry_run"


def _error_report(ctx: Context, error: dict[str, Any]) -> OptimizationReport:
    """Stub OptimizationReport used when the pipeline aborted."""
    return OptimizationReport(
        status="error",
        error_code=str(error.get("error_code") or "UNKNOWN"),
        error_message=str(error.get("message")
                          or "Optimization could not run."),
        optimized_sql=str(ctx.state.get("original_sql") or ""),
        baseline_bytes=0,
        optimized_bytes=0,
        bytes_saved=0,
        savings_pct=0.0,
        estimated_usd_before=0.0,
        estimated_usd_after=0.0,
        usd_saved_per_run=0.0,
        measurement_method="dry_run",
        baseline_measured=False,
        optimized_verified_live=False,
    )


def build_report(ctx: Context) -> OptimizationReport:
    """Assemble the report from state written by the optimize/validate loop."""
    state = ctx.state
    error = as_dict(state.get("pipeline_error"))
    if error.get("error_code"):
        return _error_report(ctx, error)

    optimizer_step = as_dict(state.get("optimizer_step"))
    validation = as_dict(state.get("validation"))
    compare = as_dict(validation.get("compare")
                      ) or as_dict(state.get("compare"))
    compare_ok = compare.get("status") == "success"

    warnings = [
        *as_str_list(validation.get("warnings")),
        *as_str_list(as_dict(state.get("metadata_step")).get("warnings")),
    ]
    if not compare_ok:
        compare = {}
        warnings.append(COMPARE_MISSING_WARNING)
    if forbids_live_execution(str(state.get("original_sql") or "")):
        warnings.append(DML_DRY_RUN_WARNING)

    return OptimizationReport(
        optimized_sql=str(
            optimizer_step.get("optimized_sql") or state.get(
                "original_sql") or ""
        ),
        baseline_bytes=as_int(compare.get("baseline_bytes")),
        optimized_bytes=as_int(compare.get("optimized_bytes")),
        bytes_saved=as_int(compare.get("bytes_saved")),
        savings_pct=as_float(compare.get("savings_pct")),
        estimated_usd_before=as_float(compare.get("estimated_usd_before")),
        estimated_usd_after=as_float(compare.get("estimated_usd_after")),
        usd_saved_per_run=as_float(compare.get("usd_saved_per_run")),
        measurement_method=_measurement_method(
            compare.get("measurement_method"),
            as_dict(state.get("optimized_measurement")).get(
                "measurement_method"),
            as_dict(state.get("baseline")).get("measurement_method"),
        ),
        changes_applied=_entries(
            optimizer_step.get("changes_applied"), ChangeApplied
        ),
        recommendations_not_applied=_entries(
            optimizer_step.get("recommendations_not_applied"),
            RecommendationNotApplied,
        ),
        warnings=dedupe(warnings),
        baseline_measured=bool(
            validation.get("baseline_measured") or state.get(
                "baseline_measured")
        ),
        optimized_verified_live=bool(
            validation.get("optimized_verified_live")
            or state.get("optimized_verified_live")
        ),
    )


def _human_bytes(num_bytes: int) -> str:
    value = float(num_bytes)
    for unit in BYTE_UNITS:
        if abs(value) < 1024.0:
            return f"{int(value)} B" if unit == "B" else f"{value:.2f} {unit}"
        value /= 1024.0
    return f"{value:.2f} PiB"


def _usd(value: float) -> str:
    if 0 < value < _MIN_USD:
        return f"< ${_MIN_USD:.4f}"
    return f"${value:,.4f}"


def _yes_no(value: bool) -> str:
    return "yes" if value else "no"


def _bullets(
    title: str,
    items: Sequence[ChangeApplied | RecommendationNotApplied],
) -> list[str]:
    if not items:
        return []
    lines = ["", f"## {title}", ""]
    lines += [f"- **{item.category}** — {item.description}" for item in items]
    return lines


def render_error_markdown(report: OptimizationReport) -> str:
    """Render an abort page instead of an optimization report."""
    return "\n".join(
        [
            "# Could not optimize this query",
            "",
            f"**Error code:** `{report.error_code}`",
            "",
            report.error_message or "Optimization could not run.",
            "",
            "The pipeline stopped before optimization.",
            "",
        ]
    )


def render_markdown(report: OptimizationReport) -> str:
    """Render the report as Markdown for the chat UI."""
    if report.status == "error":
        return render_error_markdown(report)

    lines = ["# BigQuery optimization report", ""]

    if report.bytes_saved > 0:
        lines.append(
            f"**{report.savings_pct:g}% fewer bytes processed** — "
            f"{_human_bytes(report.baseline_bytes)} -> "
            f"{_human_bytes(report.optimized_bytes)} "
            f"({_human_bytes(report.bytes_saved)} saved)"
        )
    else:
        lines.append("**No byte reduction found — keeping the original SQL.**")
        lines.append("")
        lines.append(f"Bytes processed: {_human_bytes(report.baseline_bytes)}")

    lines.append("")
    lines.append(
        f"**Estimated cost per run** — {_usd(report.estimated_usd_before)} -> "
        f"{_usd(report.estimated_usd_after)} "
        f"({_usd(report.usd_saved_per_run)} saved)"
    )

    lines += [
        "",
        "## Optimized SQL",
        "",
        "```sql",
        report.optimized_sql.strip(),
        "```",
    ]

    lines += _bullets("Changes applied", report.changes_applied)
    lines += _bullets(
        "Recommendations not applied", report.recommendations_not_applied
    )

    if report.warnings:
        lines += ["", "## Warnings", ""]
        lines += [f"- {warning}" for warning in report.warnings]

    lines += [
        "",
        "## Measurement",
        "",
        f"- Method: `{report.measurement_method}`",
        f"- Baseline measured: {_yes_no(report.baseline_measured)}",
        f"- Optimized verified live: {_yes_no(report.optimized_verified_live)}",
        "",
    ]
    return "\n".join(lines)


def render_report(ctx: Context) -> Event:
    """Emit the final report as Markdown plus a structured OptimizationReport."""
    report = build_report(ctx)
    payload = report.model_dump(mode="json")
    return Event(
        output=payload,
        content=types.Content(
            role="model",
            parts=[types.Part(text=render_markdown(report))],
        ),
        actions=EventActions(state_delta={"optimization_report": payload}),
    )
