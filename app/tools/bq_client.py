import os

from google.cloud import bigquery

_DEFAULT_MAX_BYTES = 50 * 1024**3  # 50 GiB
_DEFAULT_USD_PER_TIB = 6.25
_DEFAULT_BQ_LOCATION = "US"

_client: bigquery.Client | None = None


def get_bq_location() -> str:
    """BigQuery job location. Independent from Vertex GOOGLE_CLOUD_LOCATION."""
    location = (os.environ.get("BQ_LOCATION") or _DEFAULT_BQ_LOCATION).strip()
    if location.lower() == "global":
        raise ValueError(
            "BQ_LOCATION cannot be 'global'. BigQuery jobs need a region "
            "such as US, EU, or us-east4. Keep GOOGLE_CLOUD_LOCATION=global "
            "for Gemini and set BQ_LOCATION to your dataset region."
        )
    return location


def get_bq_client() -> bigquery.Client:
    global _client
    if _client is None:
        project = os.environ.get("GOOGLE_CLOUD_PROJECT")
        _client = bigquery.Client(project=project, location=get_bq_location())
    return _client


def get_max_bytes_billed() -> int:
    raw = os.environ.get("BQ_MAX_BYTES_BILLED")
    if raw is None:
        return _DEFAULT_MAX_BYTES
    return int(raw)


def get_usd_per_tib() -> float:
    raw = os.environ.get("BQ_ON_DEMAND_USD_PER_TIB")
    if raw is None:
        return _DEFAULT_USD_PER_TIB
    return float(raw)
