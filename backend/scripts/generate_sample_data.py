"""Regenerate backend/samples/ecommerce_sales.csv.

Usage (from backend/):  python -m scripts.generate_sample_data
"""

from pathlib import Path

from app.sample_data import SAMPLE_FILENAME, write_sample_csv

if __name__ == "__main__":
    out = write_sample_csv(Path(__file__).resolve().parent.parent / "samples" / SAMPLE_FILENAME)
    print(f"Wrote {out}")
