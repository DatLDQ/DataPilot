import pytest

from app.tools import ToolExecutor


@pytest.fixture
def executor(manager, sample_dataset, settings):
    dataset_id, profile = sample_dataset
    return ToolExecutor(manager, dataset_id, profile, settings)


def test_run_sql_returns_preview_and_ui_rows(executor):
    out = executor.execute("run_sql", {"sql": "SELECT city, count(*) n FROM ecommerce_sales GROUP BY 1 ORDER BY 2 DESC",
                                       "purpose": "orders by city"})
    assert not out.is_error
    assert out.content.startswith("city | n")
    assert "5 rows returned" in out.content
    assert out.ui["columns"] == ["city", "n"] and len(out.ui["rows"]) == 5


def test_run_sql_errors_are_returned_to_the_model(executor):
    out = executor.execute("run_sql", {"sql": "SELECT nope FROM ecommerce_sales", "purpose": "x"})
    assert out.is_error and "SQL error" in out.content
    out = executor.execute("run_sql", {"sql": "DROP TABLE ecommerce_sales", "purpose": "x"})
    assert out.is_error


def test_invalid_input(executor):
    assert executor.execute("run_sql", {"purpose": "missing sql"}).is_error
    assert executor.execute("run_sql", "not a dict").is_error
    assert executor.execute("no_such_tool", {}).is_error


@pytest.mark.parametrize(
    ("column", "kind"), [("revenue", "numeric"), ("order_date", "temporal"), ("city", "categorical")]
)
def test_profile_column(executor, column, kind):
    out = executor.execute("profile_column", {"table": "ecommerce_sales", "column": column})
    assert not out.is_error, out.content
    assert out.ui["kind"] == kind
    assert out.ui["distribution"]["rows"]
    if kind == "numeric":
        assert sum(r[1] for r in out.ui["distribution"]["rows"]) == out.ui["stats"]["non_null"]


def test_profile_column_unknown(executor):
    out = executor.execute("profile_column", {"table": "ecommerce_sales", "column": "nope"})
    assert out.is_error and "Columns:" in out.content


def test_create_chart_simple(executor):
    out = executor.execute("create_chart", {
        "title": "Revenue by channel", "chart_type": "bar",
        "sql": "SELECT channel, sum(revenue) AS revenue FROM ecommerce_sales GROUP BY 1 ORDER BY 2 DESC",
        "x": "channel", "y": ["revenue"],
    })
    assert not out.is_error, out.content
    assert out.ui["spec"]["y_keys"] == ["revenue"]
    assert len(out.ui["data"]) == 5 and set(out.ui["data"][0]) == {"channel", "revenue"}


def test_create_chart_pivots_series(executor):
    out = executor.execute("create_chart", {
        "title": "Monthly revenue by channel", "chart_type": "line",
        "sql": "SELECT strftime(order_date, '%Y-%m') AS month, channel, sum(revenue) AS revenue "
               "FROM ecommerce_sales GROUP BY ALL ORDER BY 1",
        "x": "month", "y": ["revenue"], "series": "channel",
    })
    assert not out.is_error, out.content
    assert len(out.ui["spec"]["y_keys"]) == 5
    assert len(out.ui["data"]) == 24
    assert "Mobile App" in out.ui["data"][0]


def test_create_chart_missing_column(executor):
    out = executor.execute("create_chart", {
        "title": "t", "chart_type": "bar", "sql": "SELECT city FROM ecommerce_sales", "x": "city", "y": ["revenue"],
    })
    assert out.is_error and "missing column" in out.content


def test_create_chart_folds_extra_series_into_other(executor):
    out = executor.execute("create_chart", {
        "title": "Revenue by product", "chart_type": "line",
        "sql": "SELECT strftime(order_date, '%Y') AS year, product, sum(revenue) AS revenue "
               "FROM ecommerce_sales GROUP BY ALL",
        "x": "year", "y": ["revenue"], "series": "product",
    })
    assert not out.is_error, out.content
    keys = out.ui["spec"]["y_keys"]
    assert len(keys) == 8 and keys[-1] == "Other"
    total = sum(sum(v for k, v in row.items() if k != "year") for row in out.ui["data"])
    direct = executor.execute("run_sql", {"sql": "SELECT sum(revenue) FROM ecommerce_sales", "purpose": ""})
    assert abs(total - direct.ui["rows"][0][0]) < 1
