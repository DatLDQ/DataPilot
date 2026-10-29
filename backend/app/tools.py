"""Tools exposed to the agent, their input validation, and execution."""

from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError, field_validator

from .config import Settings
from .datasets import DatasetError, DatasetManager, QueryResult, QueryTimeoutError, quote_ident
from .sql_guard import UnsafeSQLError

ChartType = Literal["bar", "horizontal_bar", "line", "area", "scatter", "pie"]

TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "name": "run_sql",
        "description": (
            "Run one read-only DuckDB SQL query against the dataset tables and get the result back. "
            "Use it to explore data, check assumptions and compute every number you report. "
            "Results are truncated to a preview, so aggregate in SQL instead of pulling raw rows."
        ),
        "eager_input_streaming": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "sql": {"type": "string", "description": "A single SELECT/WITH/DESCRIBE/SUMMARIZE statement."},
                "purpose": {"type": "string", "description": "One short sentence: what this query checks."},
            },
            "required": ["sql", "purpose"],
            "additionalProperties": False,
        },
    },
    {
        "name": "profile_column",
        "description": (
            "Get a distribution profile of one column: for numeric/date columns, quantiles and a histogram; "
            "for text columns, the most frequent values with counts. Useful before grouping or filtering."
        ),
        "eager_input_streaming": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "table": {"type": "string"},
                "column": {"type": "string"},
            },
            "required": ["table", "column"],
            "additionalProperties": False,
        },
    },
    {
        "name": "create_chart",
        "description": (
            "Render a chart for the user from a SQL query. The query must return the x column and the y "
            "column(s). For a grouped/multi-series chart, return long-format rows and set `series` to the "
            "grouping column (use a single y column then). Keep charts focused: at most ~50 categories for "
            "bar charts. The user sees the chart; you get a short confirmation and a data preview."
        ),
        "eager_input_streaming": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "chart_type": {"type": "string", "enum": ["bar", "horizontal_bar", "line", "area", "scatter", "pie"]},
                "sql": {"type": "string"},
                "x": {"type": "string", "description": "Column for the x axis (or slice label for pie)."},
                "y": {"type": "array", "items": {"type": "string"}, "description": "Numeric column(s) to plot."},
                "series": {"type": "string", "description": "Optional column to split into multiple series."},
                "description": {"type": "string", "description": "One-sentence takeaway shown under the chart."},
            },
            "required": ["title", "chart_type", "sql", "x", "y"],
            "additionalProperties": False,
        },
    },
]


class RunSQLInput(BaseModel):
    sql: str = Field(min_length=1)
    purpose: str = ""


class ProfileColumnInput(BaseModel):
    table: str = Field(min_length=1)
    column: str = Field(min_length=1)


class CreateChartInput(BaseModel):
    title: str = Field(min_length=1)
    chart_type: ChartType
    sql: str = Field(min_length=1)
    x: str = Field(min_length=1)
    y: list[str] = Field(min_length=1, max_length=8)
    series: str | None = None
    description: str | None = None

    @field_validator("series")
    @classmethod
    def _blank_series(cls, v: str | None) -> str | None:
        return v or None


INPUT_MODELS: dict[str, type[BaseModel]] = {
    "run_sql": RunSQLInput,
    "profile_column": ProfileColumnInput,
    "create_chart": CreateChartInput,
}


@dataclass
class ToolOutcome:
    content: str  # what Claude sees
    is_error: bool = False
    ui: dict[str, Any] = field(default_factory=dict)  # what the frontend renders
    summary: str = ""


def format_preview(result: QueryResult, limit: int) -> str:
    """Compact pipe-table preview for the model."""
    if not result.columns:
        return "(statement returned no columns)"
    shown = result.rows[:limit]
    lines = [" | ".join(result.columns), " | ".join("---" for _ in result.columns)]
    for row in shown:
        lines.append(" | ".join(_cell(v) for v in row))
    total = f"{len(result.rows)}{'+' if result.truncated else ''}"
    footer = f"\n({total} rows returned"
    if len(result.rows) > len(shown) or result.truncated:
        footer += f"; showing first {len(shown)}"
    footer += f"; {result.elapsed_ms} ms)"
    return "\n".join(lines) + footer


def _cell(v: Any) -> str:
    if v is None:
        return "NULL"
    if isinstance(v, float):
        return f"{v:.6g}"
    s = str(v)
    return s if len(s) <= 60 else s[:57] + "..."


class ToolExecutor:
    def __init__(self, datasets: DatasetManager, dataset_id: str, profile: dict, settings: Settings):
        self.datasets = datasets
        self.dataset_id = dataset_id
        self.settings = settings
        self.schema = {t["name"]: {c["name"]: c["type"] for c in t["columns"]} for t in profile["tables"]}

    def execute(self, name: str, raw_input: Any) -> ToolOutcome:
        model = INPUT_MODELS.get(name)
        if model is None:
            return ToolOutcome(f"Unknown tool '{name}'.", is_error=True, summary="Unknown tool")
        if not isinstance(raw_input, dict):
            return ToolOutcome(
                "INVALID_JSON: tool input must be a JSON object.", is_error=True, summary="Invalid input"
            )
        try:
            args = model.model_validate(raw_input)
        except ValidationError as exc:
            msg = "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors())
            return ToolOutcome(f"Invalid tool input - {msg}", is_error=True, summary="Invalid input")
        try:
            return getattr(self, f"_{name}")(args)
        except (UnsafeSQLError, QueryTimeoutError, DatasetError) as exc:
            return ToolOutcome(f"Error: {exc}", is_error=True, summary=str(exc))
        except Exception as exc:  # noqa: BLE001 - SQL errors go back to the model so it can fix the query
            message = str(exc).split("\n")[0][:500]
            return ToolOutcome(f"SQL error: {message}", is_error=True, summary=message)

    # -- tools ------------------------------------------------------------
    def _run_sql(self, args: RunSQLInput) -> ToolOutcome:
        result = self.datasets.query(self.dataset_id, args.sql, self.settings.ui_max_rows)
        rows_label = f"{len(result.rows)}{'+' if result.truncated else ''} rows"
        return ToolOutcome(
            content=format_preview(result, self.settings.llm_preview_rows),
            ui={
                "columns": result.columns,
                "column_types": result.column_types,
                "rows": result.rows,
                "truncated": result.truncated,
                "elapsed_ms": result.elapsed_ms,
            },
            summary=f"{rows_label} in {result.elapsed_ms} ms",
        )

    def _profile_column(self, args: ProfileColumnInput) -> ToolOutcome:
        columns = self.schema.get(args.table)
        if columns is None:
            raise DatasetError(f"Table '{args.table}' does not exist. Tables: {', '.join(self.schema)}")
        if args.column not in columns:
            raise DatasetError(f"Column '{args.column}' not in '{args.table}'. Columns: {', '.join(columns)}")
        t, c = quote_ident(args.table), quote_ident(args.column)
        col_type = columns[args.column].upper()
        numeric = any(k in col_type for k in ("INT", "DECIMAL", "DOUBLE", "FLOAT", "REAL", "NUMERIC", "HUGEINT"))
        temporal = any(k in col_type for k in ("DATE", "TIME"))
        q = lambda sql, n=100: self.datasets.query(self.dataset_id, sql, n, validate=False)  # noqa: E731

        stats = q(
            f"SELECT count(*) AS n, count({c}) AS non_null, count(DISTINCT {c}) AS distinct_values"
            + (f", min({c}) AS min, max({c}) AS max" if numeric or temporal else "")
            + (f", avg({c}) AS mean, stddev({c}) AS std, quantile_cont({c}, [0.05, 0.25, 0.5, 0.75, 0.95]) AS quantiles"
               if numeric else "")
            + f" FROM {t}"
        )
        stats_dict = dict(zip(stats.columns, stats.rows[0], strict=True))
        if numeric:
            dist = q(
                f"WITH s AS (SELECT {c}::DOUBLE AS v FROM {t} WHERE {c} IS NOT NULL), "
                f"b AS (SELECT min(v) AS lo, max(v) AS hi FROM s) "
                f"SELECT round(lo + (hi - lo) / 20 * bin, 4) AS bin_start, count(*) AS count FROM ("
                f"SELECT coalesce(least(floor((v - lo) / nullif(hi - lo, 0) * 20), 19), 0) AS bin, lo, hi "
                f"FROM s, b) GROUP BY bin, lo, hi ORDER BY bin"
            )
        elif temporal:
            dist = q(
                f"SELECT date_trunc('month', {c}::TIMESTAMP)::DATE AS month, count(*) AS count FROM {t} "
                f"WHERE {c} IS NOT NULL GROUP BY 1 ORDER BY 1", 240
            )
        else:
            dist = q(
                f"SELECT {c} AS value, count(*) AS count FROM {t} GROUP BY 1 ORDER BY 2 DESC LIMIT 25", 25
            )
        content = "Stats: " + ", ".join(f"{k}={_cell(v)}" for k, v in stats_dict.items())
        content += "\nDistribution:\n" + format_preview(dist, 40)
        return ToolOutcome(
            content=content,
            ui={"stats": stats_dict, "distribution": {"columns": dist.columns, "rows": dist.rows},
                "kind": "numeric" if numeric else "temporal" if temporal else "categorical"},
            summary=f"{args.table}.{args.column}: {stats_dict['distinct_values']} distinct values",
        )

    def _create_chart(self, args: CreateChartInput) -> ToolOutcome:
        result = self.datasets.query(self.dataset_id, args.sql, self.settings.max_chart_points)
        wanted = [args.x, *args.y, *([args.series] if args.series else [])]
        missing = [col for col in wanted if col not in result.columns]
        if missing:
            raise DatasetError(
                f"Chart query result is missing column(s) {missing}. Returned columns: {result.columns}"
            )
        if not result.rows:
            raise DatasetError("Chart query returned no rows.")
        xi = result.columns.index(args.x)
        if args.series:
            data, y_keys = self._pivot(result, args.x, args.series, args.y[0])
        else:
            yi = [result.columns.index(y) for y in args.y]
            data = [{args.x: r[xi], **{y: r[i] for y, i in zip(args.y, yi, strict=True)}} for r in result.rows]
            y_keys = args.y
        spec = {
            "title": args.title,
            "chart_type": args.chart_type,
            "x": args.x,
            "y_keys": y_keys,
            "description": args.description,
            "sql": args.sql,
        }
        note = " (truncated to the first rows)" if result.truncated else ""
        preview = format_preview(result, 15)
        return ToolOutcome(
            content=(
                f"Chart '{args.title}' rendered for the user with {len(data)} points{note}.\n"
                f"Data preview:\n{preview}"
            ),
            ui={"spec": spec, "data": data, "truncated": result.truncated},
            summary=f"{args.chart_type} chart, {len(data)} points",
        )

    def _pivot(self, result: QueryResult, x: str, series: str, y: str) -> tuple[list[dict], list[str]]:
        """Long -> wide. Beyond ``max_chart_series`` the smallest series fold into "Other"."""
        xi, si, yi = (result.columns.index(c) for c in (x, series, y))
        totals: dict[str, float] = {}
        for r in result.rows:
            key = str(r[si])
            totals[key] = totals.get(key, 0.0) + (r[yi] if isinstance(r[yi], int | float) else 0.0)
        ranked = sorted(totals, key=lambda k: -abs(totals[k]))
        cap = self.settings.max_chart_series
        keep = set(ranked if len(ranked) <= cap else ranked[: cap - 1])
        rows: dict[Any, dict] = {}
        for r in result.rows:
            key = str(r[si]) if str(r[si]) in keep else "Other"
            row = rows.setdefault(r[xi], {x: r[xi]})
            if key == "Other" and isinstance(r[yi], int | float):
                row[key] = row.get(key, 0) + r[yi]
            else:
                row[key] = r[yi]
        y_keys = [k for k in ranked if k in keep] + (["Other"] if len(keep) < len(ranked) else [])
        return list(rows.values()), y_keys
