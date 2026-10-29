"""Offline demo mode: a rule-based stand-in for Claude, so the app can be tried without an API key.

``DemoLLM`` implements the same ``LLMClient`` protocol as ``ClaudeLLM`` and sees exactly what the
model would see: the system prompt (with the dataset schema) and tool results as text. It maps the
question to one of a few analysis recipes by keyword, calls the real tools, and writes the answer
from the tables they return. Everything downstream (agent loop, SQL guard, DuckDB, charts, SSE) is real.
"""

import asyncio
import re
import uuid
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Any

from .datasets import quote_ident as q
from .llm import LLMEvent, LLMTurn

DEMO_MODEL = "demo-offline"
_ID_PREFIX = "toolu_demo_"

_NUMERIC = ("INT", "DECIMAL", "DOUBLE", "FLOAT", "REAL", "NUMERIC")
_METRIC_PRIORITY = ("revenue", "sales", "amount", "profit", "total", "value", "price", "quantity")
_AVG_HINTS = ("rating", "score", "price", "pct", "percent", "rate", "days", "age", "avg", "mean", "duration")
_GENERIC_TOKENS = {"date", "time", "type", "name", "total", "value", "count", "number", "code"}
_VI_CHARS = re.compile(r"[àáạảãâầấậẩẫăằắặẳẵèéẹẻẽêềếệểễìíịỉĩòóọỏõôồốộổỗơờớợởỡùúụủũưừứựửữỳýỵỷỹđ]")

# Phrase in the question -> substrings of the column names it refers to.
_ALIASES: dict[str, tuple[str, ...]] = {
    "doanh thu": ("revenue", "sales", "amount"),
    "doanh số": ("sales", "revenue"),
    "lợi nhuận": ("profit", "margin"),
    "đánh giá": ("rating", "score", "review"),
    "giao hàng": ("delivery", "shipping"),
    "đơn giá": ("price",),
    "số lượng": ("quantity", "qty"),
    "chi phí": ("cost",),
    "giảm giá": ("discount",),
    "kênh": ("channel",),
    "thành phố": ("city",),
    "tỉnh": ("province", "city"),
    "khu vực": ("region", "area"),
    "danh mục": ("category",),
    "ngành hàng": ("category",),
    "sản phẩm": ("product",),
    "phân khúc": ("segment",),
    "khách hàng": ("customer",),
    "sales": ("revenue",),
    "shipping": ("delivery",),
    "satisfaction": ("rating", "score"),
    "margin": ("profit",),
}

_KEYWORDS: dict[str, tuple[str, ...]] = {
    "relationship": ("affect", "impact", "correlat", "relationship", "relate", "depend", "vs", "versus",
                     "ảnh hưởng", "tương quan", "liên quan", "tác động"),
    "distribution": ("distribution", "outlier", "spread", "histogram", "skew",
                     "phân phối", "ngoại lai", "bất thường"),
    "trend": ("trend", "over time", "month", "evolve", "evolution", "growth", "grow", "year over year", "yoy",
              "season", "xu hướng", "tháng", "thời gian", "tăng trưởng", "biến động", "theo năm"),
    "overview": ("overview", "summary", "summari", "quality", "missing", "null", "describe",
                 "tổng quan", "chất lượng", "thiếu", "tóm tắt", "mô tả"),
    "ranking": ("top", "highest", "lowest", "most", "least", "best", "worst", "which", "rank", "compare",
                "breakdown", "break down", "share", "concentrat", "cao nhất", "thấp nhất", "nhiều nhất",
                "ít nhất", "so sánh", "tỷ trọng", "phân bổ", "nào"),
    "count": ("how many", "number of", "count", "records", "orders", "bao nhiêu", "số đơn", "số bản ghi"),
    "average": ("average", "avg", "mean", "trung bình"),
}


# ---------------------------------------------------------------------------
# Schema and question understanding
# ---------------------------------------------------------------------------
@dataclass
class Column:
    name: str
    type: str
    distinct: int | None = None

    @property
    def kind(self) -> str:
        t = self.type.upper()
        if "DATE" in t or "TIMESTAMP" in t:
            return "temporal"
        if any(k in t for k in _NUMERIC):
            return "id" if self.name == "id" or self.name.endswith("_id") else "numeric"
        if self.distinct is not None and 2 <= self.distinct <= 60:
            return "categorical"
        return "other"

    @property
    def pretty(self) -> str:
        return self.name.replace("_", " ")


@dataclass
class Table:
    name: str
    rows: int
    columns: list[Column] = field(default_factory=list)

    def of(self, kind: str) -> list[Column]:
        return [c for c in self.columns if c.kind == kind]


_TABLE_RE = re.compile(r"^### Table `(?P<name>[^`]+)` \((?P<rows>[\d,]+) rows\)$")
_COLUMN_RE = re.compile(r"^- (?P<name>\S+) \((?P<type>.+?)\)(?:: (?P<extras>.*))?$")


def parse_schema(system: str) -> list[Table]:
    """Read tables and columns back out of the schema section of the system prompt."""
    tables: list[Table] = []
    for line in system.splitlines():
        if m := _TABLE_RE.match(line):
            tables.append(Table(m["name"], int(m["rows"].replace(",", ""))))
        elif tables and (m := _COLUMN_RE.match(line)):
            distinct = re.search(r"~(\d+) distinct", m["extras"] or "")
            tables[-1].columns.append(Column(m["name"], m["type"], int(distinct[1]) if distinct else None))
    return tables


def columns_from_profile(table: dict) -> list[Column]:
    return [Column(c["name"], c["type"], c.get("approx_unique")) for c in table["columns"]]


def pick_metric(columns: list[Column]) -> Column | None:
    """The column most likely to be "the" business measure (revenue before quantity, etc.)."""
    nums = [c for c in columns if c.kind == "numeric"]
    for word in _METRIC_PRIORITY:
        for c in nums:
            if word in c.name:
                return c
    return nums[0] if nums else None


def _find(text: str, word: str) -> int:
    m = re.search(rf"(?<!\w){re.escape(word)}", text)
    return m.start() if m else -1


def _has(text: str, group: str) -> bool:
    return any(_find(text, w) >= 0 for w in _KEYWORDS[group])


def _mention(col: Column, text: str) -> tuple[int, int] | None:
    """(quality, position) of the best reference to ``col`` in the question, if any."""
    candidates = [(3, _find(text, col.name)), (3, _find(text, col.pretty))]
    candidates += [(2, _find(text, phrase)) for phrase, targets in _ALIASES.items()
                   if any(t in col.name for t in targets)]
    candidates += [(1, _find(text, tok)) for tok in col.name.split("_")
                   if len(tok) >= 4 and tok not in _GENERIC_TOKENS]
    found = [c for c in candidates if c[1] >= 0]
    return min(found, key=lambda c: (-c[0], c[1])) if found else None


def _mentioned(columns: list[Column], text: str) -> list[Column]:
    hits = [(m, c) for c in columns if (m := _mention(c, text))]
    return [c for m, c in sorted(hits, key=lambda h: (-h[0][0], h[0][1]))]


def _t(vi: bool, en: str, vi_text: str) -> str:
    return vi_text if vi else en


# ---------------------------------------------------------------------------
# Plans: which tools to call for a question
# ---------------------------------------------------------------------------
Call = tuple[str, str, dict[str, Any]]  # (result key, tool name, tool input)


@dataclass
class Plan:
    intent: str
    vi: bool
    table: Table
    metric: Column | None = None
    agg: str = "sum"  # sum | avg | count
    date: Column | None = None
    dim: Column | None = None
    x: Column | None = None
    y: Column | None = None
    fallback: bool = False

    @property
    def alias(self) -> str:
        if self.agg == "count" or self.metric is None:
            return "records"
        return f"{'total' if self.agg == 'sum' else 'avg'}_{self.metric.name}"

    @property
    def expr(self) -> str:
        if self.agg == "count" or self.metric is None:
            return "count(*)"
        return f"round({self.agg}({q(self.metric.name)}), 2)"

    @property
    def label(self) -> str:
        if self.agg == "count" or self.metric is None:
            return _t(self.vi, "number of records", "số bản ghi")
        p = self.metric.pretty
        if self.agg == "sum":
            return _t(self.vi, f"total {p}", f"tổng {p}")
        return _t(self.vi, f"average {p}", f"{p} trung bình")

    @property
    def tbl(self) -> str:
        return q(self.table.name)

    def month_sql(self) -> str:
        assert self.date is not None
        return f"date_trunc('month', {q(self.date.name)})::DATE"


def make_plan(question: str, tables: list[Table]) -> Plan | None:
    if not tables:
        return None
    text = question.lower()
    table = next((t for t in tables if _find(text, t.name.lower()) >= 0), tables[0])
    nums = _mentioned(table.of("numeric"), text)
    dims = _mentioned(table.of("categorical"), text)
    dates = _mentioned(table.of("temporal"), text) or table.of("temporal")
    plan = Plan("overview", bool(_VI_CHARS.search(text)), table, date=dates[0] if dates else None)

    count = _has(text, "count") and not nums
    plan.metric = nums[0] if nums else pick_metric(table.columns)
    if count or plan.metric is None:
        plan.agg = "count"
    elif _has(text, "average") or any(h in plan.metric.name for h in _AVG_HINTS):
        plan.agg = "avg"

    if _has(text, "relationship") and len(nums) >= 2:
        plan.intent = "relationship"
        plan.x, plan.y = sorted(nums[:2], key=lambda c: _mention(c, text)[1])  # type: ignore[index]
        plan.dim = dims[0] if dims else None
    elif _has(text, "distribution") and not count and plan.metric is not None:
        plan.intent = "distribution"
    elif _has(text, "trend") and plan.date is not None:
        plan.intent = "trend"
        plan.dim = dims[0] if dims else None
    elif _has(text, "overview"):
        pass
    elif dims or _has(text, "ranking") or count:
        plan.dim = dims[0] if dims else next(iter(table.of("categorical")), None)
        plan.intent = "breakdown" if plan.dim else "overview"
    else:
        plan.fallback = True
    return plan


def _calls_trend(p: Plan) -> list[Call]:
    assert p.date is not None
    d, month, a = q(p.date.name), p.month_sql(), q(p.alias)
    where = f"WHERE {d} IS NOT NULL"
    if p.dim is not None:
        k = q(p.dim.name)
        growth = (
            f"WITH y AS (SELECT {k} AS k, year({d}) AS yr, {p.expr} AS v FROM {p.tbl} {where} GROUP BY 1, 2), "
            "b AS (SELECT min(yr) AS lo, max(yr) AS hi FROM y) "
            f"SELECT k AS {k}, any_value(lo) AS from_year, any_value(hi) AS to_year, "
            "max(v) FILTER (WHERE yr = lo) AS first_value, max(v) FILTER (WHERE yr = hi) AS last_value, "
            "round(100 * (max(v) FILTER (WHERE yr = hi) / nullif(max(v) FILTER (WHERE yr = lo), 0) - 1), 1) "
            "AS growth_pct FROM y, b GROUP BY k ORDER BY growth_pct DESC NULLS LAST"
        )
        return [
            ("chart", "create_chart", {
                "title": _t(p.vi, f"{p.label.capitalize()} by month and {p.dim.pretty}",
                            f"{p.label.capitalize()} theo tháng và {p.dim.pretty}"),
                "chart_type": "line",
                "sql": f"SELECT {month} AS \"month\", {k}, {p.expr} AS {a} FROM {p.tbl} {where} "
                       "GROUP BY 1, 2 ORDER BY 1",
                "x": "month", "y": [p.alias], "series": p.dim.name,
            }),
            ("growth", "run_sql", {
                "sql": growth,
                "purpose": _t(p.vi, f"Compare {p.label} between the first and last year for each {p.dim.pretty}.",
                              f"So sánh {p.label} giữa năm đầu và năm cuối cho từng {p.dim.pretty}."),
            }),
        ]
    records = "" if p.agg == "count" else ", count(*) AS records"
    return [
        ("chart", "create_chart", {
            "title": _t(p.vi, f"{p.label.capitalize()} by month", f"{p.label.capitalize()} theo tháng"),
            "chart_type": "line",
            "sql": f"SELECT {month} AS \"month\", {p.expr} AS {a} FROM {p.tbl} {where} GROUP BY 1 ORDER BY 1",
            "x": "month", "y": [p.alias],
        }),
        ("yearly", "run_sql", {
            "sql": f"SELECT year({d}) AS \"year\", {p.expr} AS {a}{records} FROM {p.tbl} {where} "
                   "GROUP BY 1 ORDER BY 1",
            "purpose": _t(p.vi, f"Yearly {p.label}.", f"{p.label.capitalize()} theo năm."),
        }),
        ("peaks", "run_sql", {
            "sql": f"SELECT strftime({month}, '%Y-%m') AS \"month\", {p.expr} AS {a} FROM {p.tbl} {where} "
                   "GROUP BY 1 ORDER BY 2 DESC LIMIT 3",
            "purpose": _t(p.vi, "Find the strongest months.", "Tìm các tháng cao nhất."),
        }),
        ("lows", "run_sql", {
            "sql": f"SELECT strftime({month}, '%Y-%m') AS \"month\", {p.expr} AS {a} FROM {p.tbl} {where} "
                   "GROUP BY 1 ORDER BY 2 ASC LIMIT 3",
            "purpose": _t(p.vi, "Find the weakest months.", "Tìm các tháng thấp nhất."),
        }),
    ]


def _calls_breakdown(p: Plan) -> list[Call]:
    assert p.dim is not None
    k, a = q(p.dim.name), q(p.alias)
    m = q(p.metric.name) if p.metric is not None else ""
    share = {
        "sum": f", round(100 * sum({m}) / sum(sum({m})) OVER (), 1) AS share_pct",
        "count": ", round(100 * count(*) / sum(count(*)) OVER (), 1) AS share_pct",
        "avg": "",
    }[p.agg]
    records = "" if p.agg == "count" else ", count(*) AS records"
    return [
        ("ranking", "run_sql", {
            "sql": f"SELECT {k}, {p.expr} AS {a}{records}{share} FROM {p.tbl} GROUP BY 1 "
                   "ORDER BY 2 DESC NULLS LAST LIMIT 20",
            "purpose": _t(p.vi, f"Rank each {p.dim.pretty} by {p.label}.",
                          f"Xếp hạng từng {p.dim.pretty} theo {p.label}."),
        }),
        ("chart", "create_chart", {
            "title": _t(p.vi, f"{p.label.capitalize()} by {p.dim.pretty}",
                        f"{p.label.capitalize()} theo {p.dim.pretty}"),
            "chart_type": "horizontal_bar",
            "sql": f"SELECT {k}, {p.expr} AS {a} FROM {p.tbl} GROUP BY 1 ORDER BY 2 DESC NULLS LAST LIMIT 15",
            "x": p.dim.name, "y": [p.alias],
        }),
    ]


def _calls_distribution(p: Plan) -> list[Call]:
    assert p.metric is not None
    c = q(p.metric.name)
    stats = (
        f"WITH s AS (SELECT quantile_cont({c}, 0.25) AS q1, quantile_cont({c}, 0.5) AS med, "
        f"quantile_cont({c}, 0.75) AS q3 FROM {p.tbl}) "
        f"SELECT count({c}) AS non_null, round(min({c}), 2) AS \"min\", round(any_value(q1), 2) AS q1, "
        f"round(any_value(med), 2) AS median, round(avg({c}), 2) AS mean, round(any_value(q3), 2) AS q3, "
        f"round(max({c}), 2) AS \"max\", "
        f"count(*) FILTER (WHERE {c} > q3 + 1.5 * (q3 - q1) OR {c} < q1 - 1.5 * (q3 - q1)) AS outliers, "
        f"round(any_value(q3 + 1.5 * (q3 - q1)), 2) AS upper_fence FROM {p.tbl}, s"
    )
    return [
        ("profile", "profile_column", {"table": p.table.name, "column": p.metric.name}),
        ("stats", "run_sql", {
            "sql": stats,
            "purpose": _t(p.vi, "Quartiles and 1.5×IQR outlier count.",
                          "Tứ phân vị và số giá trị ngoại lai (1.5×IQR)."),
        }),
        ("top", "run_sql", {
            "sql": f"SELECT * FROM {p.tbl} ORDER BY {c} DESC NULLS LAST LIMIT 5",
            "purpose": _t(p.vi, f"Inspect the rows with the largest {p.metric.pretty}.",
                          f"Xem các dòng có {p.metric.pretty} lớn nhất."),
        }),
    ]


def _calls_relationship(p: Plan) -> list[Call]:
    assert p.x is not None and p.y is not None
    x, y = q(p.x.name), q(p.y.name)
    both = f"WHERE {x} IS NOT NULL AND {y} IS NOT NULL"
    avg_y = f"avg_{p.y.name}"
    calls: list[Call] = [
        ("corr", "run_sql", {
            "sql": f"SELECT round(corr({x}, {y}), 3) AS correlation, count(*) AS pairs FROM {p.tbl} {both}",
            "purpose": _t(p.vi, f"Correlation between {p.x.pretty} and {p.y.pretty}.",
                          f"Hệ số tương quan giữa {p.x.pretty} và {p.y.pretty}."),
        }),
    ]
    if p.x.distinct is not None and p.x.distinct <= 15:
        calls.append(("chart", "create_chart", {
            "title": _t(p.vi, f"Average {p.y.pretty} by {p.x.pretty}", f"{p.y.pretty} trung bình theo {p.x.pretty}"),
            "chart_type": "bar",
            "sql": f"SELECT {x}, round(avg({y}), 2) AS {q(avg_y)}, count(*) AS records FROM {p.tbl} {both} "
                   "GROUP BY 1 ORDER BY 1",
            "x": p.x.name, "y": [avg_y],
        }))
    else:
        calls.append(("chart", "create_chart", {
            "title": f"{p.y.pretty} vs {p.x.pretty}",
            "chart_type": "scatter",
            "sql": f"SELECT {x}, {y} FROM {p.tbl} {both} LIMIT 500",
            "x": p.x.name, "y": [p.y.name],
        }))
    if p.dim is not None:
        calls.append(("by_dim", "run_sql", {
            "sql": f"SELECT {q(p.dim.name)}, round(avg({x}), 2) AS {q('avg_' + p.x.name)}, "
                   f"round(avg({y}), 2) AS {q(avg_y)}, count(*) AS records FROM {p.tbl} {both} "
                   "GROUP BY 1 ORDER BY 2 DESC LIMIT 15",
            "purpose": _t(p.vi, f"Compare both measures per {p.dim.pretty}.",
                          f"So sánh hai chỉ số theo {p.dim.pretty}."),
        }))
    return calls


def _calls_overview(p: Plan) -> list[Call]:
    dates = f", min({q(p.date.name)}) AS first_date, max({q(p.date.name)}) AS last_date" if p.date else ""
    calls: list[Call] = [
        ("summary", "run_sql", {
            "sql": f"SUMMARIZE {p.tbl}",
            "purpose": _t(p.vi, "Types, ranges, distinct counts and null rates of every column.",
                          "Kiểu, khoảng giá trị, số giá trị khác nhau và tỷ lệ null của mọi cột."),
        }),
        ("coverage", "run_sql", {
            "sql": f"SELECT count(*) AS records{dates} FROM {p.tbl}",
            "purpose": _t(p.vi, "Row count and date coverage.", "Số dòng và khoảng thời gian."),
        }),
    ]
    if p.date is not None and p.metric is not None and p.agg != "count":
        calls.append(("chart", "create_chart", {
            "title": _t(p.vi, f"{p.label.capitalize()} by month", f"{p.label.capitalize()} theo tháng"),
            "chart_type": "line",
            "sql": f"SELECT {p.month_sql()} AS \"month\", {p.expr} AS {q(p.alias)} FROM {p.tbl} "
                   f"WHERE {q(p.date.name)} IS NOT NULL GROUP BY 1 ORDER BY 1",
            "x": "month", "y": [p.alias],
        }))
    return calls


_CALLS: dict[str, Callable[[Plan], list[Call]]] = {
    "trend": _calls_trend,
    "breakdown": _calls_breakdown,
    "distribution": _calls_distribution,
    "relationship": _calls_relationship,
    "overview": _calls_overview,
}


def _intro(p: Plan) -> tuple[str, str]:
    """(thinking summary, narration) shown before the tool calls."""
    vi, L = p.vi, p.label
    if p.intent == "trend":
        assert p.date is not None
        extra = (_t(vi, f"the growth of each {p.dim.pretty}", f"mức tăng trưởng của từng {p.dim.pretty}") if p.dim
                 else _t(vi, "yearly totals and the strongest and weakest months",
                         "tổng theo năm và các tháng cao, thấp nhất"))
        think = _t(vi, f"The question is about change over time. `{p.date.name}` is the date column and I'll "
                       f"measure {L}, aggregated by month for the chart, then pull {extra} to put numbers on it.",
                   f"Câu hỏi về biến động theo thời gian. `{p.date.name}` là cột ngày, tôi sẽ đo {L} gộp theo "
                   f"tháng để vẽ biểu đồ, rồi tính {extra} để có số liệu cụ thể.")
        say = _t(vi, f"I'll chart {L} by month and compute {extra}.", f"Tôi sẽ vẽ {L} theo tháng và tính {extra}.")
    elif p.intent == "breakdown":
        assert p.dim is not None
        think = _t(vi, f"This is a comparison across `{p.dim.name}`. I'll rank the groups by {L} and look at "
                       "each group's share to see how concentrated it is.",
                   f"Đây là phép so sánh giữa các nhóm `{p.dim.name}`. Tôi sẽ xếp hạng theo {L} và xem tỷ "
                   "trọng từng nhóm để đánh giá mức độ tập trung.")
        say = _t(vi, f"Let me rank {p.dim.pretty} by {L} and chart it.",
                 f"Tôi sẽ xếp hạng {p.dim.pretty} theo {L} và vẽ biểu đồ.")
    elif p.intent == "distribution":
        assert p.metric is not None
        think = _t(vi, f"To describe `{p.metric.name}` I need its quartiles and spread. I'll profile the column for "
                       "a histogram, count outliers with the 1.5×IQR rule and pull the largest rows to inspect.",
                   f"Để mô tả `{p.metric.name}` tôi cần tứ phân vị và độ phân tán. Tôi sẽ profile cột để có "
                   "histogram, đếm ngoại lai theo quy tắc 1.5×IQR và xem các dòng lớn nhất.")
        say = _t(vi, f"I'll profile `{p.metric.name}` and check for outliers.",
                 f"Tôi sẽ profile cột `{p.metric.name}` và kiểm tra ngoại lai.")
    elif p.intent == "relationship":
        assert p.x is not None and p.y is not None
        per = f" and per {p.dim.pretty}" if p.dim else ""
        per_vi = f" và theo từng {p.dim.pretty}" if p.dim else ""
        think = _t(vi, f"To see whether `{p.x.name}` relates to `{p.y.name}`, I'll compute their correlation and "
                       f"compare the average {p.y.pretty} at each level of {p.x.pretty}{per}.",
                   f"Để xem `{p.x.name}` có liên quan tới `{p.y.name}` không, tôi sẽ tính hệ số tương quan và so "
                   f"sánh {p.y.pretty} trung bình ở từng mức {p.x.pretty}{per_vi}.")
        say = _t(vi, f"Let me measure how `{p.x.name}` and `{p.y.name}` move together.",
                 f"Tôi sẽ đo mức độ `{p.x.name}` và `{p.y.name}` biến động cùng nhau.")
    else:
        think = _t(vi, "For an overview I'll summarise every column (types, ranges, distinct counts, null rates) "
                       "and check the row count and date coverage.",
                   "Để có cái nhìn tổng quan, tôi sẽ tóm tắt mọi cột (kiểu, khoảng giá trị, số giá trị khác nhau, "
                   "tỷ lệ null) và kiểm tra số dòng cùng khoảng thời gian.")
        if p.fallback:
            think = _t(vi, "This doesn't match a question type demo mode understands, so I'll fall back to a "
                           "general overview. ",
                       "Câu hỏi này không thuộc các dạng mà chế độ demo hiểu được, nên tôi sẽ đưa ra tổng quan "
                       "chung. ") + think
        say = _t(vi, "I'll start with a summary of every column and the overall coverage.",
                 "Tôi sẽ bắt đầu bằng bản tóm tắt mọi cột và phạm vi dữ liệu.")
    return think, say


# ---------------------------------------------------------------------------
# Answers: written from the tool results
# ---------------------------------------------------------------------------
class _NoData(Exception):
    pass


@dataclass
class Results:
    by_key: dict[str, tuple[str, bool]]  # key -> (content, is_error)

    def rows(self, key: str) -> list[dict[str, str]]:
        content, is_error = self.by_key.get(key, ("", True))
        return [] if is_error else parse_table(content)

    def errors(self) -> list[str]:
        return [content for content, is_error in self.by_key.values() if is_error]


def parse_table(content: str) -> list[dict[str, str]]:
    """Parse the pipe-table preview that tools return to the model."""
    lines = content.splitlines()
    for i in range(len(lines) - 1):
        if re.fullmatch(r"---( \| ---)*", lines[i + 1]):
            header = lines[i].split(" | ")
            rows = []
            for line in lines[i + 2:]:
                if not line or line.startswith("("):
                    break
                cells = line.split(" | ")
                if len(cells) == len(header):
                    rows.append(dict(zip(header, cells, strict=True)))
            return rows
    return []


def _num(s: str | None) -> float | None:
    try:
        return float(str(s).rstrip("%").replace(",", ""))
    except (TypeError, ValueError):
        return None


def _fmt(v: float | None) -> str:
    if v is None:
        return "n/a"
    if abs(v) >= 1000:
        return f"{v:,.0f}"
    if float(v).is_integer():
        return str(int(v))
    return f"{v:,.2f}"


def _pct(v: float | None) -> str:
    return "n/a" if v is None else f"{v:+.1f}%"


def _label(v: str) -> str:
    return "(missing)" if v == "NULL" else v


def _md_table(headers: list[str], rows: list[list[str]]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    lines += ["| " + " | ".join(r) + " |" for r in rows]
    return "\n".join(lines)


def _answer_trend(p: Plan, r: Results) -> str:
    vi, L = p.vi, p.label
    if p.dim is not None:
        rows = [row for row in r.rows("growth") if _num(row.get("growth_pct")) is not None]
        if not rows:
            raise _NoData
        lo, hi, k = rows[0]["from_year"], rows[0]["to_year"], p.dim.name
        top, bottom = rows[0], rows[-1]
        tg, bg = _num(top["growth_pct"]), _num(bottom["growth_pct"]) or 0.0
        head = _t(vi,
                  f"From {lo} to {hi}, **{_label(top[k])}** grew fastest: {L} went from "
                  f"{_fmt(_num(top['first_value']))} to {_fmt(_num(top['last_value']))} (**{_pct(tg)}**). "
                  f"**{_label(bottom[k])}** {'grew the least' if bg >= 0 else 'declined the most'} ({_pct(bg)}).",
                  f"Từ {lo} đến {hi}, **{_label(top[k])}** tăng trưởng nhanh nhất: {L} đi từ "
                  f"{_fmt(_num(top['first_value']))} lên {_fmt(_num(top['last_value']))} (**{_pct(tg)}**). "
                  f"**{_label(bottom[k])}** {'tăng chậm nhất' if bg >= 0 else 'giảm mạnh nhất'} ({_pct(bg)}).")
        table = _md_table([p.dim.pretty, lo, hi, _t(vi, "Growth", "Tăng trưởng")],
                          [[_label(row[k]), _fmt(_num(row["first_value"])), _fmt(_num(row["last_value"])),
                            _pct(_num(row["growth_pct"]))] for row in rows[:8]])
        tail = _t(vi, f"The chart above shows the monthly {L} of each {p.dim.pretty}.",
                  f"Biểu đồ phía trên thể hiện {L} theo tháng của từng {p.dim.pretty}.")
        return f"{head}\n\n{table}\n\n{tail}"

    yearly, peaks, lows = r.rows("yearly"), r.rows("peaks"), r.rows("lows")
    if not yearly:
        raise _NoData
    a = p.alias
    first, last = yearly[0], yearly[-1]
    v0, v1 = _num(first[a]), _num(last[a])
    if len(yearly) >= 2:
        change = f" (**{_pct(100 * (v1 / v0 - 1))}**)" if v0 and v1 is not None else ""
        head = _t(vi, f"{L.capitalize()} went from **{_fmt(v0)}** in {first['year']} to **{_fmt(v1)}** in "
                      f"{last['year']}{change}.",
                  f"{L.capitalize()} đi từ **{_fmt(v0)}** năm {first['year']} lên **{_fmt(v1)}** năm "
                  f"{last['year']}{change}.")
    else:
        head = _t(vi, f"{L.capitalize()} in {first['year']} was **{_fmt(v0)}**.",
                  f"{L.capitalize()} năm {first['year']} là **{_fmt(v0)}**.")
    months = lambda rows: ", ".join(f"**{m['month']}** ({_fmt(_num(m[a]))})" for m in rows)  # noqa: E731
    body = []
    if peaks:
        body.append(_t(vi, f"The strongest months were {months(peaks)}", f"Các tháng cao nhất là {months(peaks)}"))
    if lows:
        body.append(_t(vi, f"the weakest were {months(lows)}.", f"thấp nhất là {months(lows)}."))
    headers = [_t(vi, "Year", "Năm"), L.capitalize()] + (["Records"] if "records" in first and a != "records" else [])
    table = _md_table(headers, [[row["year"], _fmt(_num(row[a]))]
                                + ([_fmt(_num(row["records"]))] if len(headers) == 3 else []) for row in yearly])
    caveat = _t(vi, "Partial months at the edges of the data can look artificially low.",
                "Các tháng không đủ dữ liệu ở đầu hoặc cuối giai đoạn có thể trông thấp bất thường.")
    return f"{head} {'; '.join(body)}\n\n{table}\n\n{caveat}"


def _answer_breakdown(p: Plan, r: Results) -> str:
    assert p.dim is not None
    rows = r.rows("ranking")
    if not rows:
        raise _NoData
    vi, L, a, k = p.vi, p.label, p.alias, p.dim.name
    top = rows[0]
    has_share = "share_pct" in top
    if p.agg == "avg":
        low = rows[-1]
        head = _t(vi, f"**{_label(top[k])}** has the highest {L} (**{_fmt(_num(top[a]))}**) and "
                      f"**{_label(low[k])}** the lowest (**{_fmt(_num(low[a]))}**).",
                  f"**{_label(top[k])}** có {L} cao nhất (**{_fmt(_num(top[a]))}**), còn "
                  f"**{_label(low[k])}** thấp nhất (**{_fmt(_num(low[a]))}**).")
    else:
        share = f" ({top['share_pct']}%)" if has_share else ""
        others = ", ".join(f"**{_label(row[k])}** ({_fmt(_num(row[a]))})" for row in rows[1:3])
        head = _t(vi, f"**{_label(top[k])}** leads with {L} of **{_fmt(_num(top[a]))}**{share}"
                      + (f", followed by {others}." if others else "."),
                  f"**{_label(top[k])}** dẫn đầu với {L} là **{_fmt(_num(top[a]))}**{share}"
                  + (f", tiếp theo là {others}." if others else "."))
    if has_share and len(rows) > 3:
        top3 = sum(_num(row["share_pct"]) or 0 for row in rows[:3])
        n = f"{len(rows)}{'+' if len(rows) == 20 else ''}"
        head += " " + _t(vi, f"The top 3 account for **{top3:.1f}%** of the total across {n} groups.",
                         f"Top 3 chiếm **{top3:.1f}%** tổng số trong {n} nhóm.")
    headers = [p.dim.pretty, L.capitalize()] + ([_t(vi, "Share", "Tỷ trọng")] if has_share else [])
    table = _md_table(headers, [[_label(row[k]), _fmt(_num(row[a]))]
                                + ([f"{row['share_pct']}%"] if has_share else []) for row in rows[:10]])
    return f"{head}\n\n{table}"


def _answer_distribution(p: Plan, r: Results) -> str:
    assert p.metric is not None
    rows = r.rows("stats")
    if not rows:
        raise _NoData
    s = {k: _num(v) for k, v in rows[0].items()}
    vi, name = p.vi, p.metric.pretty
    parts = [_t(vi, f"**{name}** ranges from **{_fmt(s['min'])}** to **{_fmt(s['max'])}**. The median is "
                    f"**{_fmt(s['median'])}** and the mean **{_fmt(s['mean'])}**; the middle half of the values "
                    f"falls between **{_fmt(s['q1'])}** and **{_fmt(s['q3'])}**.",
                f"**{name}** nằm trong khoảng **{_fmt(s['min'])}** đến **{_fmt(s['max'])}**. Trung vị là "
                f"**{_fmt(s['median'])}**, trung bình **{_fmt(s['mean'])}**; một nửa số giá trị ở giữa nằm trong "
                f"khoảng **{_fmt(s['q1'])}** đến **{_fmt(s['q3'])}**.")]
    mean, med = s["mean"] or 0, s["median"] or 0
    if med > 0 and mean > med * 1.15:
        parts.append(_t(vi, "The mean sits well above the median, so the distribution is right-skewed: a minority "
                            "of large values pulls the average up.",
                        "Trung bình cao hơn hẳn trung vị nên phân phối lệch phải: một số ít giá trị lớn kéo "
                        "trung bình lên."))
    elif med > 0 and mean < med * 0.85:
        parts.append(_t(vi, "The mean is well below the median, so the distribution is left-skewed.",
                        "Trung bình thấp hơn hẳn trung vị nên phân phối lệch trái."))
    out, n = s["outliers"] or 0, s["non_null"] or 0
    if out:
        share = f"{out / n:.1%}" if n else "n/a"
        parts.append(_t(vi, f"Using the 1.5×IQR rule, **{_fmt(out)}** values ({share}) are outliers; the upper fence "
                            f"is {_fmt(s['upper_fence'])}. The largest rows are listed in the last query above.",
                        f"Theo quy tắc 1.5×IQR có **{_fmt(out)}** giá trị ngoại lai ({share}); ngưỡng trên là "
                        f"{_fmt(s['upper_fence'])}. Các dòng lớn nhất nằm ở truy vấn cuối phía trên."))
    else:
        parts.append(_t(vi, "No values fall outside the 1.5×IQR fences.",
                        "Không có giá trị nào nằm ngoài ngưỡng 1.5×IQR."))
    return "\n\n".join(parts)


def _answer_relationship(p: Plan, r: Results) -> str:
    assert p.x is not None and p.y is not None
    rows = r.rows("corr")
    if not rows:
        raise _NoData
    vi, x, y = p.vi, p.x.pretty, p.y.pretty
    corr, pairs = _num(rows[0]["correlation"]), _num(rows[0]["pairs"])
    c = abs(corr or 0)
    strength = (_t(vi, "almost no", "gần như không có") if c < 0.1 else _t(vi, "a weak", "yếu") if c < 0.3
                else _t(vi, "a moderate", "trung bình") if c < 0.5 else _t(vi, "a strong", "mạnh"))
    direction = _t(vi, "negative" if (corr or 0) < 0 else "positive", "nghịch" if (corr or 0) < 0 else "thuận")
    if c < 0.1:
        head = _t(vi, f"There is {strength} linear relationship between **{x}** and **{y}** (correlation "
                      f"**{_fmt(corr)}** across {_fmt(pairs)} rows).",
                  f"**{x}** và **{y}** {strength} quan hệ tuyến tính (hệ số tương quan **{_fmt(corr)}** trên "
                  f"{_fmt(pairs)} dòng).")
    else:
        head = _t(vi, f"There is {strength} {direction} relationship between **{x}** and **{y}** (correlation "
                      f"**{_fmt(corr)}** across {_fmt(pairs)} rows).",
                  f"**{x}** và **{y}** có tương quan {direction} mức {strength} (hệ số **{_fmt(corr)}** trên "
                  f"{_fmt(pairs)} dòng).")
    parts = [head]
    groups = r.rows("chart")
    avg_y = f"avg_{p.y.name}"
    if len(groups) >= 2 and avg_y in groups[0]:
        g0, g1 = groups[0], groups[-1]
        parts.append(_t(vi, f"Average {y} is **{_fmt(_num(g0[avg_y]))}** when {x} = {g0[p.x.name]} and "
                            f"**{_fmt(_num(g1[avg_y]))}** when {x} = {g1[p.x.name]} (see the chart above).",
                        f"{y} trung bình là **{_fmt(_num(g0[avg_y]))}** khi {x} = {g0[p.x.name]} và "
                        f"**{_fmt(_num(g1[avg_y]))}** khi {x} = {g1[p.x.name]} (xem biểu đồ phía trên)."))
    by_dim = r.rows("by_dim")
    if p.dim is not None and by_dim:
        avg_x, k = f"avg_{p.x.name}", p.dim.name
        hi, lo = by_dim[0], by_dim[-1]
        parts.append(_t(vi, f"By {p.dim.pretty}: **{_label(hi[k])}** has the highest average {x} "
                            f"({_fmt(_num(hi[avg_x]))}) with an average {y} of {_fmt(_num(hi[avg_y]))}, while "
                            f"**{_label(lo[k])}** has the lowest ({_fmt(_num(lo[avg_x]))}, {y} "
                            f"{_fmt(_num(lo[avg_y]))}).",
                        f"Theo {p.dim.pretty}: **{_label(hi[k])}** có {x} trung bình cao nhất "
                        f"({_fmt(_num(hi[avg_x]))}) với {y} trung bình {_fmt(_num(hi[avg_y]))}, còn "
                        f"**{_label(lo[k])}** thấp nhất ({_fmt(_num(lo[avg_x]))}, {y} {_fmt(_num(lo[avg_y]))})."))
        parts.append(_md_table([p.dim.pretty, f"avg {x}", f"avg {y}", "records"],
                               [[_label(row[k]), _fmt(_num(row[avg_x])), _fmt(_num(row[avg_y])),
                                 _fmt(_num(row["records"]))] for row in by_dim[:10]]))
    parts.append(_t(vi, "Correlation is not causation: other factors may drive both measures.",
                    "Tương quan không có nghĩa là nhân quả: có thể có yếu tố khác tác động lên cả hai."))
    return "\n\n".join(parts)


def _answer_overview(p: Plan, r: Results) -> str:
    summary, coverage = r.rows("summary"), r.rows("coverage")
    if not summary or not coverage:
        raise _NoData
    vi, cov = p.vi, coverage[0]
    n = _num(cov["records"]) or 0
    span = ""
    if "first_date" in cov:
        span = _t(vi, f", covering **{cov['first_date'][:10]}** to **{cov['last_date'][:10]}**",
                  f", từ **{cov['first_date'][:10]}** đến **{cov['last_date'][:10]}**")
    t = p.table
    kinds = _t(vi, f"{len(t.of('numeric'))} numeric, {len(t.of('categorical'))} categorical and "
                   f"{len(t.of('temporal'))} date columns",
               f"{len(t.of('numeric'))} cột số, {len(t.of('categorical'))} cột phân loại và "
               f"{len(t.of('temporal'))} cột ngày")
    parts = [_t(vi, f"**{t.name}** has **{_fmt(n)} rows** and **{len(summary)} columns**{span} ({kinds}).",
                f"Bảng **{t.name}** có **{_fmt(n)} dòng** và **{len(summary)} cột**{span} ({kinds}).")]
    nulls = sorted(((row["column_name"], _num(row.get("null_percentage")) or 0) for row in summary),
                   key=lambda c: -c[1])
    nulls = [c for c in nulls if c[1] > 0]
    if nulls:
        listed = ", ".join(f"**{name}** ({pct:.1f}%)" for name, pct in nulls[:5])
        parts.append(_t(vi, f"Data quality: missing values in {listed}. Decide whether to exclude or impute them "
                            "before averaging those columns.",
                        f"Chất lượng dữ liệu: có giá trị thiếu ở {listed}. Cần quyết định loại bỏ hay bổ sung "
                        "trước khi tính trung bình các cột này."))
    else:
        parts.append(_t(vi, "Data quality: no column has missing values.",
                        "Chất lượng dữ liệu: không cột nào có giá trị thiếu."))
    ids = [row["column_name"] for row in summary if n and (_num(row.get("approx_unique")) or 0) >= 0.95 * n]
    if ids:
        parts.append(_t(vi, f"`{ids[0]}` looks like a unique identifier.",
                        f"`{ids[0]}` có vẻ là mã định danh duy nhất."))
    ideas = []
    if p.date is not None and p.metric is not None:
        ideas.append(_t(vi, f"How has {p.label} changed month by month?",
                        f"{p.label.capitalize()} thay đổi theo tháng ra sao?"))
    dim = next(iter(t.of("categorical")), None)
    if dim is not None:
        ideas.append(_t(vi, f"Which {dim.pretty} has the highest {p.label}?",
                        f"{dim.pretty} nào có {p.label} cao nhất?"))
    if p.metric is not None:
        ideas.append(_t(vi, f"What does the distribution of {p.metric.pretty} look like?",
                        f"Phân phối của {p.metric.pretty} trông như thế nào?"))
    if ideas:
        parts.append(_t(vi, "Questions to try next:", "Có thể hỏi tiếp:") + "\n" + "\n".join(f"- {i}" for i in ideas))
    if p.fallback:
        parts.insert(0, _t(vi, "Demo mode only recognises a few question types (overview, trends, rankings, "
                               "distributions, relationships between two numeric columns), so here is a general "
                               "overview instead.",
                           "Chế độ demo chỉ hiểu một số dạng câu hỏi (tổng quan, xu hướng, xếp hạng, phân phối, "
                           "quan hệ giữa hai cột số), nên đây là phần tổng quan chung."))
    return "\n\n".join(parts)


_ANSWERS: dict[str, Callable[[Plan, Results], str]] = {
    "trend": _answer_trend,
    "breakdown": _answer_breakdown,
    "distribution": _answer_distribution,
    "relationship": _answer_relationship,
    "overview": _answer_overview,
}


def compose_answer(plan: Plan | None, results: Results | None) -> str:
    vi = bool(plan and plan.vi)
    if plan is None:
        body = _t(vi, "Demo mode could not read the dataset schema.", "Chế độ demo không đọc được schema của dữ liệu.")
    else:
        try:
            body = _ANSWERS[plan.intent](plan, results or Results({}))
        except (_NoData, KeyError, TypeError, ZeroDivisionError):
            body = _t(vi, "The queries ran (see the cards above), but the demo agent could not summarise these "
                          "results automatically.",
                      "Các truy vấn đã chạy (xem các thẻ phía trên) nhưng agent demo không tự tóm tắt được "
                      "kết quả này.")
    if results and (errors := results.errors()):
        body += "\n\n" + _t(vi, "One of the queries failed: ", "Một truy vấn bị lỗi: ") + f"`{errors[0][:200]}`"
    note = _t(vi, "*Demo mode: this answer was assembled from the query results above by an offline, rule-based "
                  "agent, not by an LLM. Set `ANTHROPIC_API_KEY` (or `GEMINI_API_KEY`) to get real analysis.*",
              "*Chế độ demo: câu trả lời này được ghép từ kết quả truy vấn ở trên bởi một agent theo luật chạy "
              "offline, không phải LLM. Đặt `ANTHROPIC_API_KEY` (hoặc `GEMINI_API_KEY`) để có phân tích thật.*")
    return f"{body}\n\n> {note}"


# ---------------------------------------------------------------------------
# The LLMClient implementation
# ---------------------------------------------------------------------------
def _last_question(messages: list[dict]) -> str:
    for msg in reversed(messages):
        if msg["role"] != "user":
            continue
        content = msg["content"]
        if isinstance(content, str):
            return content
        if any(b.get("type") == "tool_result" for b in content):
            continue
        return " ".join(b.get("text", "") for b in content if b.get("type") == "text")
    return ""


def _tool_results(messages: list[dict]) -> Results | None:
    last = messages[-1] if messages else None
    if not last or last["role"] != "user" or not isinstance(last["content"], list):
        return None
    by_key: dict[str, tuple[str, bool]] = {}
    for block in last["content"]:
        if block.get("type") != "tool_result":
            continue
        tid = block["tool_use_id"]
        key = tid[len(_ID_PREFIX):].rsplit("_", 1)[0] if tid.startswith(_ID_PREFIX) else tid
        content = block.get("content", "")
        if isinstance(content, list):
            content = "\n".join(b.get("text", "") for b in content if b.get("type") == "text")
        by_key[key] = (content, bool(block.get("is_error")))
    return Results(by_key) if by_key else None


class DemoLLM:
    """Offline ``LLMClient``: plans tool calls from keywords, then answers from the tool results."""

    model = DEMO_MODEL

    def __init__(self, delay_s: float = 0.02):
        self.delay_s = delay_s  # pause between streamed chunks so the UI feels live

    async def _chunks(self, text: str, kind: str) -> AsyncIterator[LLMEvent]:
        words = text.split(" ")
        for i in range(0, len(words), 4):
            if self.delay_s:
                await asyncio.sleep(self.delay_s)
            yield LLMEvent(kind, {"text": " ".join(words[i:i + 4]) + (" " if i + 4 < len(words) else "")})

    async def stream(
        self, *, system: str, tools: list[dict], messages: list[dict], tool_choice: dict | None = None
    ) -> AsyncIterator[LLMEvent | LLMTurn]:
        plan = make_plan(_last_question(messages), parse_schema(system))
        results = _tool_results(messages)
        content: list[dict[str, Any]] = []
        if plan is None or results is not None or tool_choice == {"type": "none"}:
            answer = compose_answer(plan, results)
            yield LLMEvent("text_start")
            async for ev in self._chunks(answer, "text_delta"):
                yield ev
            content.append({"type": "text", "text": answer})
            stop_reason = "end_turn"
        else:
            think, say = _intro(plan)
            yield LLMEvent("thinking_start")
            async for ev in self._chunks(think, "thinking_delta"):
                yield ev
            yield LLMEvent("text_start")
            async for ev in self._chunks(say, "text_delta"):
                yield ev
            # The thinking is streamed but not stored: a fake signature would be rejected if the
            # conversation is later continued with the real model.
            content.append({"type": "text", "text": say})
            for key, name, tool_input in _CALLS[plan.intent](plan):
                block = {"type": "tool_use", "id": f"{_ID_PREFIX}{key}_{uuid.uuid4().hex[:8]}", "name": name,
                         "input": tool_input}
                yield LLMEvent("tool_use_start", {"id": block["id"], "name": name})
                content.append(block)
            stop_reason = "tool_use"
        yield LLMTurn(
            content=content,
            stop_reason=stop_reason,
            model=DEMO_MODEL,
            usage={"input_tokens": 0, "output_tokens": 0, "cache_read_input_tokens": 0,
                   "cache_creation_input_tokens": 0},
        )

    async def suggest_questions(self, prompt: str) -> list[str]:
        return []  # the API falls back to heuristic suggestions, which match the demo's recipes
