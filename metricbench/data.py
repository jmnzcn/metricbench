"""Download the Olist dataset, check it, and build the two databases the benchmark uses.

- data/olist_raw.duckdb: the nine Olist CSVs as tables, untouched. The SQL agents query this one.
- data/olist.duckdb: the dbt project (olist_dbt/) built on top, which the semantic layer queries.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import urllib.request
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
RAW_DIR = DATA / "raw"
RAW_DB = DATA / "olist_raw.duckdb"
SEMANTIC_DB = DATA / "olist.duckdb"
DBT_DIR = ROOT / "olist_dbt"

# Olist's Kaggle release (CC BY-NC-SA 4.0). Kaggle needs a login, so the files come from a Hugging Face
# mirror; the hashes match two other independent mirrors and Hugging Face's own LFS hashes.
MIRROR = "https://huggingface.co/datasets/bulutttt/olist-raw-data/resolve/main/"
FILES = {
    "olist_customers_dataset.csv": "983a422239e1712ded753b3bf9ecf47dc73f144d306029dcfa99e70a226883d2",
    "olist_geolocation_dataset.csv": "b514f6fc991b9566aeba02aa5d67e2c3630f034b60a0e05aa0d082a3b66d88d6",
    "olist_order_items_dataset.csv": "0bc4d068c4fe38cbb01bd90e8746e3c613fe7b4baef75fab7b0e329701c3e279",
    "olist_order_payments_dataset.csv": "4f713964f2815dbbaa40b9488268c55aac3627bfce5aa96cf58d1f3616de3cc0",
    "olist_order_reviews_dataset.csv": "012b61c7593e34f51fa614efdf802b9c7056ce6aae5307ddb93236e7cfc797d7",
    "olist_orders_dataset.csv": "8df58ef3d2d7e9944010f7beecd9b75367f5588ec6e3c91cec19ae3345ef9ecf",
    "olist_products_dataset.csv": "3e6569628a17fbc75fd206ee357b59e20364b9afa90f5b6cd5b4d624c58aa9cc",
    "olist_sellers_dataset.csv": "1f643d2b950373b85735e7794b20986f528d7a000432e7c6f9bcbb44d0846a0e",
    "product_category_name_translation.csv": "a81f0d1f27b27e7293f761bc79e3ce8f348ee39c4b3ed3e49bde38f478586278",
}

# Raw table name -> CSV. Geolocation is left out: no question needs it and it is the largest file.
RAW_TABLES = {
    "orders": "olist_orders_dataset.csv",
    "order_items": "olist_order_items_dataset.csv",
    "order_payments": "olist_order_payments_dataset.csv",
    "order_reviews": "olist_order_reviews_dataset.csv",
    "customers": "olist_customers_dataset.csv",
    "products": "olist_products_dataset.csv",
    "sellers": "olist_sellers_dataset.csv",
    "product_category_name_translation": "product_category_name_translation.csv",
}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def fetch() -> None:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    for name in RAW_TABLES.values():
        expected = FILES[name]
        path = RAW_DIR / name
        if not path.exists() or sha256(path) != expected:
            print(f"downloading {name}")
            urllib.request.urlretrieve(MIRROR + name, path)
        if (actual := sha256(path)) != expected:
            raise SystemExit(f"{name}: SHA-256 {actual}, expected {expected}")
    print(f"{len(RAW_TABLES)} files verified in {RAW_DIR}")


def build_raw() -> None:
    RAW_DB.unlink(missing_ok=True)
    with duckdb.connect(str(RAW_DB)) as con:
        for table, csv in RAW_TABLES.items():
            # Zip code prefixes have leading zeros.
            zip_column = {"customers": "customer_zip_code_prefix", "sellers": "seller_zip_code_prefix"}.get(table)
            types = f", types = {{'{zip_column}': 'VARCHAR'}}" if zip_column else ""
            con.execute(f"create table {table} as select * from read_csv('{(RAW_DIR / csv).as_posix()}', header = true{types})")
    print(f"built {RAW_DB}")


def dbt(*args: str) -> None:
    exe = Path(sys.executable).with_name("dbt.exe" if os.name == "nt" else "dbt")
    env = {**os.environ, "METRICBENCH_RAW_DIR": str(RAW_DIR), "METRICBENCH_DB": str(SEMANTIC_DB)}
    subprocess.run([str(exe), *args, "--profiles-dir", "."], cwd=DBT_DIR, env=env, check=True)


def build_semantic() -> None:
    dbt("build")
    dbt("parse")  # writes target/semantic_manifest.json for MetricFlow
    print(f"built {SEMANTIC_DB}")


def main() -> None:
    fetch()
    build_raw()
    build_semantic()


if __name__ == "__main__":
    main()
