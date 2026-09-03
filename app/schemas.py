from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

MeasurementMethod = Literal["dry_run", "live", "mixed"]
Status = Literal["success", "error"]


def dump_model(model_cls: type[BaseModel], data: dict) -> dict:
    """Validate `data` and return a JSON-serializable dict."""
    return model_cls.model_validate(data).model_dump(mode="json", by_alias=True)


class PipelineInput(BaseModel):
    sql: str = Field(description="BigQuery SQL to optimize (SELECT / WITH).")
    context: str | None = Field(
        default=None,
        description="Optional business or pipeline context for the rewrite.",
    )


class ColumnField(BaseModel):
    name: str = Field(description="Output column name.")
    type: str = Field(description="BigQuery type, e.g. STRING, INT64, TIMESTAMP.")
    mode: str | None = Field(
        default=None,
        description="NULLABLE, REQUIRED, or REPEATED.",
    )


class TableRef(BaseModel):
    project: str
    dataset: str
    table: str


class TimePartitioning(BaseModel):
    type: str | None = None
    field: str | None = None
    require_partition_filter: bool | None = None
    expiration_ms: int | None = None


class RangeBound(BaseModel):
    start: int | None = None
    end: int | None = None
    interval: int | None = None


class RangePartitioning(BaseModel):
    field: str | None = None
    range: RangeBound | None = None


class ForeignKeyColumnRef(BaseModel):
    referencing_column: str
    referenced_column: str


class ForeignKey(BaseModel):
    name: str | None = None
    referenced_table: str | None = None
    column_references: list[ForeignKeyColumnRef] = Field(default_factory=list)


class TableMetadata(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    status: Status
    project: str | None = None
    dataset: str | None = None
    table: str | None = None
    table_type: str | None = None
    num_bytes: int | None = None
    num_rows: int | None = None
    time_partitioning: TimePartitioning | None = None
    range_partitioning: RangePartitioning | None = None
    clustering_fields: list[str] = Field(default_factory=list)
    primary_key_columns: list[str] = Field(default_factory=list)
    foreign_keys: list[ForeignKey] = Field(default_factory=list)
    require_partition_filter: bool = False
    schema_fields: list[ColumnField] = Field(
        default_factory=list, alias="schema")
    error_message: str | None = None


class PartitionSummary(BaseModel):
    status: Status
    partition_count: int | None = None
    min_partition_id: str | None = None
    max_partition_id: str | None = None
    total_rows: int | None = None
    total_logical_bytes: int | None = None
    error_message: str | None = None


class ViewDefinition(BaseModel):
    status: Status
    project: str | None = None
    dataset: str | None = None
    view: str | None = None
    view_query: str | None = None
    error_message: str | None = None


class QueryMeasurement(BaseModel):
    status: Status
    total_bytes_processed: int | None = None
    measurement_method: MeasurementMethod | None = None
    referenced_tables: list[TableRef] = Field(default_factory=list)
    output_schema: list[ColumnField] = Field(default_factory=list)
    statement_type: str | None = None
    job_id: str | None = None
    dry_run_bytes: int | None = None
    error_message: str | None = None


class AntipatternFinding(BaseModel):
    pattern: str
    severity: str
    message: str


class AntipatternResult(BaseModel):
    status: Literal["success", "error"] = "success"
    findings: list[AntipatternFinding] = Field(default_factory=list)
    error_message: str | None = None


class SchemaCheck(BaseModel):
    status: Literal["success", "error"] = "success"
    passed: bool = False
    mismatches: list[str] = Field(default_factory=list)
    error_message: str | None = None


class CompareResult(BaseModel):
    status: Literal["success", "error"] = "success"
    baseline_bytes: int = 0
    optimized_bytes: int = 0
    bytes_saved: int = 0
    savings_pct: float = 0.0
    estimated_usd_before: float = 0.0
    estimated_usd_after: float = 0.0
    usd_saved_per_run: float = 0.0
    measurement_method: MeasurementMethod | None = None
    warnings: list[str] = Field(default_factory=list)
    error_message: str | None = None


class MetadataStep(BaseModel):
    table_refs: list[str] = Field(
        default_factory=list,
        description="Fully-qualified table refs collected via tools.",
    )
    baseline_measured: bool = Field(
        default=False,
        description="Whether measure_query_cost(purpose=baseline) succeeded.",
    )
    warnings: list[str] = Field(default_factory=list)


class ChangeApplied(BaseModel):
    category: str = Field(description="Category of optimization applied.")
    description: str = Field(description="What changed and why.")


class RecommendationNotApplied(BaseModel):
    category: str = Field(description="Category of recommendation.")
    description: str = Field(description="Why it was not applied in the SQL.")


class OptimizerStep(BaseModel):
    optimized_sql: str = Field(description="Rewritten BigQuery SQL.")
    changes_applied: list[ChangeApplied] = Field(default_factory=list)
    recommendations_not_applied: list[RecommendationNotApplied] = Field(
        default_factory=list
    )


class ValidationStep(BaseModel):
    passed: bool = Field(
        default=False,
        description="True when schema check passed and measurements succeeded.",
    )
    schema_check: SchemaCheck = Field(default_factory=SchemaCheck)
    compare: CompareResult = Field(default_factory=CompareResult)
    warnings: list[str] = Field(default_factory=list)
    retry_hint: str | None = Field(
        default=None,
        description="What the optimizer should fix if validation failed.",
    )
    baseline_measured: bool = False
    optimized_verified_live: bool = Field(
        default=False,
        description=(
            "True when the optimized measurement used live or mixed "
            "(dry-run could not estimate bytes). Informational; "
            "not a validation gate."
        ),
    )


class OptimizationReport(BaseModel):
    optimized_sql: str = Field(description="The optimized BigQuery SQL.")
    baseline_bytes: int = Field(
        description="Bytes processed by the original query.")
    optimized_bytes: int = Field(
        description="Bytes processed by the optimized query.")
    bytes_saved: int = Field(
        description="baseline_bytes minus optimized_bytes.")
    savings_pct: float = Field(description="Percentage of bytes saved.")
    estimated_usd_before: float = Field(
        description="Estimated on-demand USD for baseline.")
    estimated_usd_after: float = Field(
        description="Estimated on-demand USD for optimized.")
    usd_saved_per_run: float = Field(description="USD saved per query run.")
    measurement_method: MeasurementMethod = Field(
        description="How bytes were measured."
    )
    changes_applied: list[ChangeApplied] = Field(default_factory=list)
    recommendations_not_applied: list[RecommendationNotApplied] = Field(
        default_factory=list
    )
    warnings: list[str] = Field(default_factory=list)
    baseline_measured: bool = Field(
        description="Whether baseline was measured.")
    optimized_verified_live: bool = Field(
        description=(
            "Whether the optimized measurement used live or mixed. "
            "Informational; dry-run is sufficient when clustering is absent "
            "and dry-run returns bytes."
        ),
    )
