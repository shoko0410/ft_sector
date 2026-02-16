"""Check null-rate threshold for an input column."""
# pyright: reportMissingTypeStubs=false,reportUnknownMemberType=false,reportUnknownVariableType=false,reportUnknownArgumentType=false

from __future__ import annotations

import argparse
from pathlib import Path
from typing import cast

import pandas as pd


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="QA: null-rate checker")
    _ = parser.add_argument("--input", required=True)
    _ = parser.add_argument("--column", required=True)
    _ = parser.add_argument("--max-null", type=float, required=True)
    return parser


def _load(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise RuntimeError(f"input file does not exist: {path.as_posix()}")
    if path.suffix.lower() == ".csv":
        return pd.read_csv(path)
    return pd.read_parquet(path)


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    input_path = Path(cast(str, args.input))
    column = cast(str, args.column)
    max_null = cast(float, args.max_null)

    try:
        frame = _load(input_path)
    except RuntimeError as exc:
        print("result=FAIL")
        print(f"error={exc}")
        return 1

    if column not in frame.columns:
        print("result=FAIL")
        print(f"error=missing column: {column}")
        return 1

    null_rate = float(frame[column].isna().mean())
    passed = null_rate <= max_null
    print(f"result={'PASS' if passed else 'FAIL'}")
    print(f"column={column}")
    print(f"null_rate={null_rate:.6f}")
    print(f"max_null={max_null:.6f}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
