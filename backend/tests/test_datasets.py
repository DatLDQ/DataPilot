import json

import pandas as pd
import pytest

from app.datasets import DatasetError, QueryTimeoutError, to_identifier


def test_to_identifier_handles_vietnamese_and_symbols():
    assert to_identifier("Doanh thu (VNĐ)") == "doanh_thu_vnd"
    assert to_identifier("Thành phố") == "thanh_pho"
    assert to_identifier("2024 Sales") == "col_2024_sales"
    assert to_identifier("***") == "col"


def test_ingest_profiles_sample(sample_dataset):
    _, profile = sample_dataset
    [table] = profile["tables"]
    assert table["name"] == "ecommerce_sales"
    assert table["row_count"] > 7000
    names = {c["name"] for c in table["columns"]}
    assert {"order_date", "city", "revenue", "rating"} <= names
    rating = next(c for c in table["columns"] if c["name"] == "rating")
    assert float(rating["null_pct"]) > 5
    assert len(table["sample"]["rows"]) == 5
    json.dumps(profile)  # must be JSON serialisable


def test_ingest_excel_multi_sheet_and_messy_headers(manager, tmp_path):
    path = tmp_path / "Báo cáo.xlsx"
    with pd.ExcelWriter(path) as writer:
        pd.DataFrame({"Tên SP": ["A", "B"], "Số lượng": [1, 2], "Số lượng ": [3, 4]}).to_excel(
            writer, sheet_name="Đơn hàng", index=False)
        pd.DataFrame({"x": [1]}).to_excel(writer, sheet_name="Khác", index=False)
    _, profile = manager.ingest(path, path.name)
    tables = {t["name"]: t for t in profile["tables"]}
    assert set(tables) == {"don_hang", "khac"}
    assert [c["name"] for c in tables["don_hang"]["columns"]] == ["ten_sp", "so_luong", "so_luong_1"]


def test_rejects_unsupported_and_broken_files(manager, tmp_path):
    with pytest.raises(DatasetError):
        manager.ingest(tmp_path / "x.exe", "x.exe")
    bad = tmp_path / "bad.parquet"
    bad.write_bytes(b"not a parquet file")
    with pytest.raises(DatasetError):
        manager.ingest(bad, "bad.parquet")
    assert list(manager.root.iterdir()) == []  # failed ingests leave nothing behind


def test_query_truncates_and_serialises(manager, sample_dataset):
    dataset_id, _ = sample_dataset
    result = manager.query(dataset_id, "SELECT order_date, revenue FROM ecommerce_sales ORDER BY 1", 10)
    assert len(result.rows) == 10 and result.truncated
    assert isinstance(result.rows[0][0], str)  # dates become ISO strings


def test_connection_is_sandboxed(manager, sample_dataset, tmp_path):
    dataset_id, _ = sample_dataset
    secret = tmp_path / "secret.csv"
    secret.write_text("a\n1\n")
    # Even with the SQL guard bypassed, the engine refuses writes and file access.
    with pytest.raises(Exception, match="(?i)read-only|read only"):
        manager.query(dataset_id, "CREATE TABLE x AS SELECT 1", 10, validate=False)
    with pytest.raises(Exception, match="(?i)external access|disabled"):
        manager.query(dataset_id, f"SELECT * FROM read_csv_auto('{secret.as_posix()}')", 10, validate=False)
    with pytest.raises(Exception, match="(?i)lock|configuration|cannot"):
        manager.query(dataset_id, "SET enable_external_access = true", 10, validate=False)


def test_query_timeout(manager, sample_dataset):
    dataset_id, _ = sample_dataset
    manager.sql_timeout_s = 0.3
    with pytest.raises(QueryTimeoutError):
        manager.query(dataset_id, "SELECT count(*) FROM range(10000000000) a", 1)
