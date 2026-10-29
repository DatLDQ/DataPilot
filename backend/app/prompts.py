import json

SYSTEM_TEMPLATE = """You are DataPilot, a senior data analyst. You answer questions about the user's dataset by \
querying it with the tools provided, then explain what the data shows.

## How to work
- Ground every number you report in a query you ran in this conversation. If you could not verify something, say so.
- Explore before concluding when the question is open-ended: check row counts, value ranges, nulls and \
  distinct values of the columns you rely on. Mention data-quality issues that affect the answer.
- Aggregate in SQL rather than pulling raw rows; tool results only show a preview.
- Use `create_chart` when a visual makes the answer clearer (trends, rankings, comparisons, distributions). \
  One or two well-chosen charts beat many.
- If the question is ambiguous, make a reasonable assumption, state it, and proceed.

## SQL dialect
DuckDB. Useful functions: date_trunc, strftime, extract, quantile_cont, median, corr, regr_slope, \
string_agg, arg_max, FILTER (WHERE ...), QUALIFY, window functions. Quote identifiers with double quotes only \
when needed. Only read-only statements are allowed.

## Final answer
Write the final answer in Markdown in the same language the user wrote in. Lead with the direct answer, \
then the supporting numbers (a small table is fine), then caveats or suggested follow-ups when useful. \
Do not paste SQL into the final answer; the user can already see every query you ran.

## Dataset: {dataset_name}
{schema}
"""


def render_schema(profile: dict) -> str:
    parts = []
    for table in profile["tables"]:
        cols = []
        for c in table["columns"]:
            detail = f"- {c['name']} ({c['type']})"
            extras = []
            if c.get("null_pct") not in (None, 0, 0.0, "0.00"):
                extras.append(f"{c['null_pct']}% null")
            if c.get("approx_unique") is not None:
                extras.append(f"~{c['approx_unique']} distinct")
            if c.get("min") is not None and c.get("max") is not None:
                lo, hi = str(c["min"])[:40], str(c["max"])[:40]
                extras.append(f"range {lo} .. {hi}")
            if extras:
                detail += ": " + ", ".join(extras)
            cols.append(detail)
        sample = table["sample"]
        sample_rows = "\n".join(json.dumps(dict(zip(sample["columns"], r, strict=False)), ensure_ascii=False)
                                for r in sample["rows"][:3])
        parts.append(
            f"### Table `{table['name']}` ({table['row_count']:,} rows)\n" + "\n".join(cols)
            + f"\nSample rows:\n{sample_rows}"
        )
    return "\n\n".join(parts)


def build_system_prompt(dataset_name: str, profile: dict) -> str:
    return SYSTEM_TEMPLATE.format(dataset_name=dataset_name, schema=render_schema(profile))


SUGGESTIONS_PROMPT = """Here is the schema of a dataset the user just uploaded:

{schema}

Suggest 4 interesting, specific analysis questions a business user might ask about this data. \
Each question should be answerable with SQL on these tables and fit on one line. Mix: one overview / \
data-quality question, one trend or comparison, one ranking, and one deeper "why" or segment question. \
Write them in English, but keep column values (city names, categories) exactly as they appear in the data."""
