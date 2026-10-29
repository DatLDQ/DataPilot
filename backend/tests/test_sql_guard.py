import pytest

from app.sql_guard import UnsafeSQLError, validate_sql


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT 1",
        "select city, sum(revenue) from sales group by 1 order by 2 desc;",
        "WITH t AS (SELECT * FROM sales) SELECT count(*) FROM t",
        "DESCRIBE sales",
        "SUMMARIZE sales",
        "FROM sales LIMIT 5",
        "SELECT 'drop table x; delete' AS s",  # keywords inside literals are fine
        'SELECT "update" FROM sales',  # ... and inside quoted identifiers
        "SELECT 1 -- ; drop table sales",
    ],
)
def test_allows_read_only_queries(sql):
    assert validate_sql(sql)


@pytest.mark.parametrize(
    "sql",
    [
        "DROP TABLE sales",
        "DELETE FROM sales",
        "INSERT INTO sales VALUES (1)",
        "SELECT 1; DROP TABLE sales",
        "ATTACH 'other.db' AS o",
        "COPY sales TO 'out.csv'",
        "INSTALL httpfs",
        "SET enable_external_access = true",
        "PRAGMA database_list",
        "SELECT * FROM read_csv_auto('C:/secrets.csv')",
        "SELECT * FROM READ_PARQUET ('x.parquet')",
        "SELECT * FROM glob('*')",
        "SELECT getenv('ANTHROPIC_API_KEY')",
        "SELECT * FROM 'C:/Windows/win.ini'",
        "WITH x AS (SELECT 1) DELETE FROM sales",
        "",
        "   ",
    ],
)
def test_rejects_unsafe_queries(sql):
    with pytest.raises(UnsafeSQLError):
        validate_sql(sql)


def test_strips_trailing_semicolon():
    assert validate_sql("SELECT 1;  ") == "SELECT 1"
