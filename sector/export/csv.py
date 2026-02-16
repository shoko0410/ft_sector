"""Export model-ready current snapshot into country CSV files."""
# pyright: reportMissingTypeStubs=false,reportUnknownMemberType=false,reportUnknownVariableType=false,reportUnknownArgumentType=false

from __future__ import annotations

import argparse
from pathlib import Path
from typing import cast

import pandas as pd

COUNTRIES = ("us", "kr", "jp")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Export country CSVs from current snapshot")
    _ = parser.add_argument("--input", required=True)
    _ = parser.add_argument("--outdir", required=True)
    return parser


def _load_input(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise RuntimeError(f"input parquet does not exist: {path.as_posix()}")
    frame = pd.read_parquet(path)
    required = {"country", "stock_code", "stock_name"}
    missing = sorted(required.difference(frame.columns))
    if missing:
        raise RuntimeError("input parquet missing required columns: " + ",".join(missing))
    return frame


def _normalize_stock_code(value: object, country: str) -> str:
    text = str(value)
    if country == "kr":
        return text.zfill(6)
    return text


def export_csv(input_path: Path, outdir: Path) -> dict[str, int]:
    frame = _load_input(input_path)
    outdir.mkdir(parents=True, exist_ok=True)
    row_counts: dict[str, int] = {}

    for country in COUNTRIES:
        chunk = frame[frame["country"].astype(str).str.lower() == country].copy()
        chunk["country"] = country
        stock_codes = cast(list[object], cast(pd.Series, chunk["stock_code"]).to_list())
        chunk["stock_code"] = [_normalize_stock_code(value, country) for value in stock_codes]
        output_path = outdir / f"{country}_sector_current.csv"
        _ = chunk.to_csv(output_path, index=False)
        row_counts[country] = int(len(chunk))

    return row_counts


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    input_path = Path(cast(str, args.input))
    outdir = Path(cast(str, args.outdir))

    try:
        row_counts = export_csv(input_path=input_path, outdir=outdir)
    except RuntimeError as exc:
        print("result=FAIL")
        print(f"error={exc}")
        return 1

    print("result=PASS")
    for country in COUNTRIES:
        output_path = outdir / f"{country}_sector_current.csv"
        print(f"{country}_path={output_path.as_posix()}")
        print(f"{country}_rows={row_counts[country]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
