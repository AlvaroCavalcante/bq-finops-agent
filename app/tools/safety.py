import re

_FORBIDDEN_ANYWHERE = re.compile(
    r"\b("
    r"TRUNCATE|DROP|CREATE|ALTER|"
    r"GRANT|REVOKE|EXPORT|LOAD|COPY|EXECUTE|CALL|SCRIPT"
    r")\b",
    re.IGNORECASE,
)
_STANDALONE_DML = re.compile(r"\b(UPDATE|DELETE)\b", re.IGNORECASE)
_NO_LIVE = re.compile(r"\b(INSERT|MERGE)\b", re.IGNORECASE)
_ALLOWED_PREFIXES = ("SELECT", "WITH", "INSERT", "MERGE")

EMPTY_SQL = "EMPTY_SQL"
FORBIDDEN_SQL = "FORBIDDEN_SQL"
INVALID_SQL = "INVALID_SQL"
FORBIDDEN_SQL_MESSAGE = (
    "Only SELECT / WITH / INSERT / MERGE queries are allowed."
)
INVALID_SQL_MESSAGE = (
    "Query must start with SELECT, WITH, INSERT, or MERGE."
)
LIVE_FORBIDDEN_MESSAGE = "Live execution is not allowed for INSERT / MERGE."


def _norm_sql(sql: str) -> str:
    return " ".join(sql.split()).rstrip(";").strip().upper()


def _twice_copy(compact: str) -> str | None:
    if len(compact) < 16:
        return None
    if len(compact) % 2 == 1:
        mid = len(compact) // 2
        if compact[mid] == " " and compact[:mid] == compact[mid + 1:]:
            return compact[:mid]
    if len(compact) % 2 == 0:
        mid = len(compact) // 2
        if compact[:mid] == compact[mid:]:
            return compact[:mid]
    return None


def _prefix_matching_norm(sql: str, target_norm: str) -> str:
    stripped = sql.strip()
    last = 0
    for match in re.finditer(r"\S+", stripped):
        last = match.end()
        if _norm_sql(stripped[:last]) == target_norm:
            return stripped[:last].rstrip(";").strip()
    return stripped.rstrip(";").strip() or sql


def dedupe_repeated_statement(sql: str) -> str:
    """Return one copy when the same statement was concatenated twice.

    Downstream agents sometimes see original_sql plus conversation history and
    pass MERGE/INSERT to BigQuery twice, which inflates dry-run bytes.
    """
    stripped = sql.strip()
    if not stripped:
        return sql

    parts = [p.strip() for p in re.split(r";\s*", stripped) if p.strip()]
    if len(parts) >= 2 and len({_norm_sql(p) for p in parts}) == 1:
        return parts[0]

    one = _twice_copy(_norm_sql(stripped))
    if one is None:
        return sql
    return _prefix_matching_norm(stripped, one)


def classify_sql_guard(sql: str) -> tuple[str, str] | None:
    """Return (error_code, message) when SQL cannot enter the optimizer pipeline."""
    stripped = sql.strip().rstrip(";")
    if not stripped:
        return EMPTY_SQL, "Empty SQL statement."

    if _FORBIDDEN_ANYWHERE.search(stripped):
        return FORBIDDEN_SQL, FORBIDDEN_SQL_MESSAGE

    if not stripped.upper().startswith(_ALLOWED_PREFIXES):
        return (
            (FORBIDDEN_SQL, FORBIDDEN_SQL_MESSAGE)
            if _STANDALONE_DML.match(stripped)
            else (INVALID_SQL, INVALID_SQL_MESSAGE)
        )

    # UPDATE/DELETE clauses are valid inside MERGE; standalone UPDATE/DELETE
    # after WITH is not.
    if _STANDALONE_DML.search(stripped) and not forbids_live_execution(stripped):
        return FORBIDDEN_SQL, FORBIDDEN_SQL_MESSAGE

    return None


def forbids_live_execution(sql: str) -> bool:
    """Return whether live BigQuery execution must never run this SQL."""
    return bool(_NO_LIVE.search(sql.strip().rstrip(";")))


def is_readonly_select(sql: str) -> tuple[bool, str | None]:
    """Return whether SQL may be executed live (read-only SELECT/WITH)."""
    classified = classify_sql_guard(sql)
    if classified is not None:
        return False, classified[1]
    if forbids_live_execution(sql):
        return False, LIVE_FORBIDDEN_MESSAGE
    return True, None
