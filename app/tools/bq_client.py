import os

from google.cloud import bigquery

_DEFAULT_MAX_BYTES = 50 * 1024**3  # 50 GiB
_DEFAULT_USD_PER_TIB = 6.25

_client: bigquery.Client | None = None


def get_bq_client() -> bigquery.Client:
    global _client
    if _client is None:
        project = os.environ.get("GOOGLE_CLOUD_PROJECT")
        location = os.environ.get("BQ_LOCATION") or os.environ.get(
            "GOOGLE_CLOUD_LOCATION", "US"
        )
        _client = bigquery.Client(project=project, location=location)
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
