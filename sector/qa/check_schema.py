"""Check required schema fields for parquet outputs."""
# pyright: reportMissingTypeStubs=false,reportUnknownMemberType=false,reportUnknownVariableType=false,reportUnknownArgumentType=false

from __future__ import annotations

import argparse
from pathlib import Path
from typing import cast

import pandas as pd


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="QA: parquet schema checker")
    _ = parser.add_argument("--input", required=True)
    _ = parser.add_argument("--required", required=True, help="comma-separated required columns")
    return parser


def _parse_required(raw: str) -> list[str]:
    values = [item.strip() for item in raw.split(",") if item.strip()]
    if not values:
        raise RuntimeError("--required must include at least one column")
    return values


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    input_path = Path(cast(str, args.input))

    try:
        required = _parse_required(cast(str, args.required))
        if not input_path.exists():
            raise RuntimeError(f"input parquet does not exist: {input_path.as_posix()}")
        frame = pd.read_parquet(input_path)
    except RuntimeError as exc:
        print("result=FAIL")
        print(f"error={exc}")
        return 1

    missing = [column for column in required if column not in frame.columns]
    if missing:
        print("result=FAIL")
        print("error=missing columns: " + ",".join(missing))
        return 1

    print("result=PASS")
    print(f"checked_columns={len(required)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
