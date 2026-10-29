"""Static validation for model-generated SQL.

This is the first of three layers of defence. The other two are enforced by
DuckDB itself: datasets are opened ``read_only`` and with
``enable_external_access=false`` + ``lock_configuration=true``, so even a query
that slips past this check cannot write, read files, or re-enable access.
"""

import re

ALLOWED_LEADING = {"select", "with", "from", "values", "describe", "summarize", "show", "pivot", "unpivot"}

FORBIDDEN_KEYWORDS = {
    "insert", "update", "delete", "merge", "drop", "create", "alter", "truncate",
    "attach", "detach", "copy", "export", "import", "install", "load", "force",
    "pragma", "set", "reset", "call", "checkpoint", "vacuum", "grant", "revoke",
    "begin", "commit", "rollback", "use",
}

# Table functions that touch the filesystem or network.
FORBIDDEN_FUNCTIONS = re.compile(
    r"\b(read_\w+|glob|parquet_\w+|sniff_csv|iceberg_\w+|delta_scan|query_table|getenv|current_setting)\s*\(",
    re.IGNORECASE,
)

_TOKEN_RE = re.compile(
    r"""
    (?P<line_comment>--[^\n]*)
  | (?P<block_comment>/\*.*?\*/)
  | (?P<squote>'(?:[^']|'')*')
  | (?P<dquote>"(?:[^"]|"")*")
  | (?P<other>.)
    """,
    re.VERBOSE | re.DOTALL,
)


class UnsafeSQLError(ValueError):
    pass


def _strip_comments_and_literals(sql: str) -> tuple[str, str]:
    """Return (sql_without_comments, skeleton) where the skeleton also blanks string literals
    and quoted identifiers so keyword checks only see real SQL tokens."""
    kept: list[str] = []
    skeleton: list[str] = []
    for m in _TOKEN_RE.finditer(sql):
        kind = m.lastgroup
        text = m.group(0)
        if kind in ("line_comment", "block_comment"):
            kept.append(" ")
            skeleton.append(" ")
        elif kind in ("squote", "dquote"):
            kept.append(text)
            skeleton.append(" '' " if kind == "squote" else " _ident_ ")
        else:
            kept.append(text)
            skeleton.append(text)
    return "".join(kept), "".join(skeleton)


def validate_sql(sql: str) -> str:
    """Validate a single read-only statement and return it normalised (no trailing semicolon)."""
    if not sql or not sql.strip():
        raise UnsafeSQLError("Query is empty.")
    if len(sql) > 20_000:
        raise UnsafeSQLError("Query is too long (max 20,000 characters).")

    cleaned, skeleton = _strip_comments_and_literals(sql)
    cleaned = cleaned.strip().rstrip(";").strip()
    skeleton = skeleton.strip().rstrip(";").strip()

    if ";" in skeleton:
        raise UnsafeSQLError("Only a single SQL statement is allowed.")

    words = re.findall(r"[A-Za-z_]+", skeleton.lower())
    if not words:
        raise UnsafeSQLError("Query has no SQL keywords.")
    if words[0] not in ALLOWED_LEADING:
        raise UnsafeSQLError(
            f"Only read-only queries are allowed (statement starts with '{words[0].upper()}')."
        )
    bad = sorted(FORBIDDEN_KEYWORDS.intersection(words))
    if bad:
        raise UnsafeSQLError(f"Forbidden keyword(s): {', '.join(w.upper() for w in bad)}.")
    fn = FORBIDDEN_FUNCTIONS.search(skeleton)
    if fn:
        raise UnsafeSQLError(f"Function '{fn.group(1)}' is not allowed; query the loaded tables instead.")
    # Replacement scans like FROM 'file.csv' read files directly.
    if re.search(r"\b(from|join)\s+''", skeleton, re.IGNORECASE):
        raise UnsafeSQLError("Reading files by path is not allowed; query the loaded tables instead.")
    return cleaned
