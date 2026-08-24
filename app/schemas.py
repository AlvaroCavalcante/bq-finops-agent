from typing import Literal

from pydantic import BaseModel, Field


class ChangeApplied(BaseModel):
    category: str = Field(description="Category of optimization applied.")
    description: str = Field(description="What changed and why.")


class RecommendationNotApplied(BaseModel):
    category: str = Field(description="Category of recommendation.")
    description: str = Field(description="Why it was not applied in the SQL.")


class OptimizationReport(BaseModel):
    optimized_sql: str = Field(description="The optimized BigQuery SQL.")
    baseline_bytes: int = Field(description="Bytes processed by the original query.")
    optimized_bytes: int = Field(description="Bytes processed by the optimized query.")
    bytes_saved: int = Field(description="baseline_bytes minus optimized_bytes.")
    savings_pct: float = Field(description="Percentage of bytes saved.")
    estimated_usd_before: float = Field(description="Estimated on-demand USD for baseline.")
    estimated_usd_after: float = Field(description="Estimated on-demand USD for optimized.")
    usd_saved_per_run: float = Field(description="USD saved per query run.")
    measurement_method: Literal["dry_run", "live", "mixed"] = Field(
        description="How bytes were measured."
    )
    changes_applied: list[ChangeApplied] = Field(default_factory=list)
    recommendations_not_applied: list[RecommendationNotApplied] = Field(
        default_factory=list
    )
    warnings: list[str] = Field(default_factory=list)
    baseline_measured: bool = Field(description="Whether baseline was measured.")
    optimized_verified_live: bool = Field(
        description="Whether optimized query ran live at least once."
    )
