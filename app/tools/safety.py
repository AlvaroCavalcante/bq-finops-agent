import re

_FORBIDDEN = re.compile(
    r"\b("
    r"INSERT|UPDATE|DELETE|MERGE|TRUNCATE|DROP|CREATE|ALTER|"
    r"GRANT|REVOKE|EXPORT|LOAD|COPY|EXECUTE|CALL|SCRIPT"
    r")\b",
    re.IGNORECASE,
)

EMPTY_SQL = "EMPTY_SQL"
FORBIDDEN_SQL = "FORBIDDEN_SQL"
INVALID_SQL = "INVALID_SQL"


def classify_sql_guard(sql: str) -> tuple[str, str] | None:
    """Return (error_code, message) when SQL is not a read-only SELECT/WITH."""
    stripped = sql.strip().rstrip(";")
    if not stripped:
        return EMPTY_SQL, "Empty SQL statement."

    if _FORBIDDEN.search(stripped):
        return FORBIDDEN_SQL, "Only SELECT / WITH … SELECT queries are allowed."

    upper = stripped.upper()
    if not (upper.startswith("SELECT") or upper.startswith("WITH")):
        return INVALID_SQL, "Query must start with SELECT or WITH."

    return None


def is_readonly_select(sql: str) -> tuple[bool, str | None]:
    """Return whether SQL is a read-only SELECT/WITH query."""
    classified = classify_sql_guard(sql)
    if classified is None:
        return True, None
    return False, classified[1]
