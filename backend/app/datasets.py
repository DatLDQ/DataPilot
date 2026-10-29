"""Dataset ingestion, profiling and sandboxed query execution on DuckDB."""

import math
import re
import shutil
import threading
import time
import unicodedata
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from datetime import time as dtime
from decimal import Decimal
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd

from .sql_guard import validate_sql

SUPPORTED_EXTENSIONS = {".csv", ".tsv", ".txt", ".parquet", ".json", ".jsonl", ".ndjson", ".xlsx", ".xls"}
MAX_TABLES = 20


class DatasetError(ValueError):
    pass


class QueryTimeoutError(RuntimeError):
    pass


@dataclass
class QueryResult:
    columns: list[str]
    column_types: list[str]
    rows: list[list[Any]]
    truncated: bool
    elapsed_ms: int
    extra: dict = field(default_factory=dict)


def to_identifier(name: str, fallback: str = "col") -> str:
    """Normalise a header (incl. Vietnamese diacritics) to a snake_case SQL identifier."""
    text = str(name).replace("đ", "d").replace("Đ", "D")
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"[^0-9a-zA-Z]+", "_", text).strip("_").lower()
    if not text:
        text = fallback
    if text[0].isdigit():
        text = f"{fallback}_{text}"
    return text


def _dedupe(names: list[str]) -> list[str]:
    seen: dict[str, int] = {}
    out = []
    for n in names:
        if n in seen:
            seen[n] += 1
            out.append(f"{n}_{seen[n]}")
        else:
            seen[n] = 0
            out.append(n)
    return out


def quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def to_jsonable(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, bool | int | str):
        return value
    if isinstance(value, float):
        return None if math.isnan(value) or math.isinf(value) else value
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime | date | dtime):
        return value.isoformat()
    if isinstance(value, timedelta):
        return str(value)
    if isinstance(value, bytes):
        return value.hex()
    if isinstance(value, list | tuple):
        return [to_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {str(k): to_jsonable(v) for k, v in value.items()}
    if hasattr(value, "item"):  # numpy scalars
        return to_jsonable(value.item())
    return str(value)


class DatasetManager:
    def __init__(self, root: Path, sql_timeout_s: float = 20.0):
        self.root = root / "datasets"
        self.root.mkdir(parents=True, exist_ok=True)
        self.sql_timeout_s = sql_timeout_s
        self._conns: dict[str, duckdb.DuckDBPyConnection] = {}
        self._lock = threading.Lock()

    def db_path(self, dataset_id: str) -> Path:
        if not re.fullmatch(r"[0-9a-f]{12}", dataset_id):
            raise DatasetError("Invalid dataset id.")
        return self.root / dataset_id / "data.duckdb"

    # -- ingestion --------------------------------------------------------
    def ingest(self, source: Path, original_name: str) -> tuple[str, dict]:
        ext = Path(original_name).suffix.lower()
        if ext not in SUPPORTED_EXTENSIONS:
            raise DatasetError(
                f"Unsupported file type '{ext}'. Supported: {', '.join(sorted(SUPPORTED_EXTENSIONS))}"
            )
        dataset_id = uuid.uuid4().hex[:12]
        db_path = self.db_path(dataset_id)
        db_path.parent.mkdir(parents=True)
        base_name = to_identifier(Path(original_name).stem, fallback="t")
        try:
            con = duckdb.connect(str(db_path))
            try:
                if ext in {".xlsx", ".xls"}:
                    sheets = pd.read_excel(source, sheet_name=None)
                    sheets = {k: v for k, v in sheets.items() if not v.empty}
                    if not sheets:
                        raise DatasetError("The workbook has no non-empty sheets.")
                    names = _dedupe(
                        [base_name if len(sheets) == 1 else to_identifier(s, "sheet") for s in sheets][:MAX_TABLES]
                    )
                    for table, df in zip(names, list(sheets.values())[:MAX_TABLES], strict=False):
                        con.register("_src_df", df)
                        self._create_table(con, table, "SELECT * FROM _src_df")
                        con.unregister("_src_df")
                else:
                    path = str(source).replace("'", "''")
                    reader = {
                        ".parquet": f"read_parquet('{path}')",
                        ".json": f"read_json_auto('{path}')",
                        ".jsonl": f"read_json_auto('{path}', format='newline_delimited')",
                        ".ndjson": f"read_json_auto('{path}', format='newline_delimited')",
                        ".tsv": f"read_csv_auto('{path}', delim='\\t', sample_size=-1)",
                    }.get(ext, f"read_csv_auto('{path}', sample_size=-1)")
                    self._create_table(con, base_name, f"SELECT * FROM {reader}")
                con.execute("CHECKPOINT")
            finally:
                con.close()
            profile = self.profile(dataset_id)
        except DatasetError:
            self.delete(dataset_id)
            raise
        except Exception as exc:  # noqa: BLE001 - surface parser errors to the user
            self.delete(dataset_id)
            raise DatasetError(f"Could not parse '{original_name}': {exc}") from exc
        return dataset_id, profile

    @staticmethod
    def _create_table(con: duckdb.DuckDBPyConnection, table: str, select_sql: str) -> None:
        con.execute(f"CREATE TEMP VIEW _src AS {select_sql}")
        original = [row[0] for row in con.execute("DESCRIBE _src").fetchall()]
        normalised = _dedupe([to_identifier(c) for c in original])
        projection = ", ".join(
            f"{quote_ident(o)} AS {quote_ident(n)}" for o, n in zip(original, normalised, strict=True)
        )
        con.execute(f"CREATE TABLE {quote_ident(table)} AS SELECT {projection} FROM _src")
        con.execute("DROP VIEW _src")

    # -- read-only access -------------------------------------------------
    def _connection(self, dataset_id: str) -> duckdb.DuckDBPyConnection:
        with self._lock:
            con = self._conns.get(dataset_id)
            if con is None:
                path = self.db_path(dataset_id)
                if not path.exists():
                    raise DatasetError("Dataset not found.")
                con = duckdb.connect(
                    str(path),
                    read_only=True,
                    config={"enable_external_access": False, "lock_configuration": True},
                )
                self._conns[dataset_id] = con
            return con

    def close(self, dataset_id: str | None = None) -> None:
        with self._lock:
            ids = [dataset_id] if dataset_id else list(self._conns)
            for i in ids:
                con = self._conns.pop(i, None)
                if con is not None:
                    con.close()

    def delete(self, dataset_id: str) -> None:
        self.close(dataset_id)
        shutil.rmtree(self.db_path(dataset_id).parent, ignore_errors=True)

    def query(self, dataset_id: str, sql: str, max_rows: int, validate: bool = True) -> QueryResult:
        """Run a read-only statement with a hard timeout. Returns at most ``max_rows`` rows."""
        if validate:
            sql = validate_sql(sql)
        cur = self._connection(dataset_id).cursor()
        timer = threading.Timer(self.sql_timeout_s, cur.interrupt)
        started = time.perf_counter()
        timer.start()
        try:
            cur.execute(sql)
            columns = [d[0] for d in cur.description] if cur.description else []
            types = [str(d[1]) for d in cur.description] if cur.description else []
            raw = cur.fetchmany(max_rows + 1)
        except duckdb.InterruptException as exc:
            raise QueryTimeoutError(f"Query exceeded the {self.sql_timeout_s:.0f}s time limit.") from exc
        finally:
            timer.cancel()
            cur.close()
        elapsed = int((time.perf_counter() - started) * 1000)
        rows = [[to_jsonable(v) for v in r] for r in raw[:max_rows]]
        return QueryResult(columns, types, rows, truncated=len(raw) > max_rows, elapsed_ms=elapsed)

    # -- profiling --------------------------------------------------------
    def profile(self, dataset_id: str) -> dict:
        tables = []
        for (table,) in self.query(dataset_id, "SHOW TABLES", 1000, validate=False).rows:
            t = quote_ident(table)
            row_count = self.query(dataset_id, f"SELECT count(*) FROM {t}", 1, validate=False).rows[0][0]
            summary = self.query(dataset_id, f"SUMMARIZE {t}", 1000, validate=False)
            idx = {c: i for i, c in enumerate(summary.columns)}
            columns = []
            for r in summary.rows:
                columns.append({
                    "name": r[idx["column_name"]],
                    "type": r[idx["column_type"]],
                    "min": r[idx["min"]],
                    "max": r[idx["max"]],
                    "approx_unique": r[idx["approx_unique"]],
                    "avg": r[idx["avg"]],
                    "null_pct": r[idx["null_percentage"]],
                })
            sample = self.query(dataset_id, f"SELECT * FROM {t} LIMIT 5", 5, validate=False)
            tables.append({
                "name": table,
                "row_count": row_count,
                "columns": columns,
                "sample": {"columns": sample.columns, "rows": sample.rows},
            })
        return {"tables": tables}
