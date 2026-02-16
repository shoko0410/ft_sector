"""Check issuer-level primary listing dedupe invariants."""
# pyright: reportMissingTypeStubs=false,reportUnknownMemberType=false,reportUnknownVariableType=false,reportUnknownArgumentType=false

from __future__ import annotations

import argparse
from pathlib import Path
from typing import cast

import pandas as pd


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="QA: primary listing dedupe")
    _ = parser.add_argument("--input", required=True)
    _ = parser.add_argument("--key", required=True)
    return parser


def _load(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise RuntimeError(f"input parquet does not exist: {path.as_posix()}")
    return pd.read_parquet(path)


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    path = Path(cast(str, args.input))
    key = cast(str, args.key)

    try:
        frame = _load(path)
    except RuntimeError as exc:
        print("result=FAIL")
        print(f"error={exc}")
        return 1

    if key not in frame.columns:
        print("result=FAIL")
        print(f"error=missing column: {key}")
        return 1

    duplicate_rows = int(frame.duplicated(subset=[key], keep=False).sum())
    passed = duplicate_rows == 0
    print(f"result={'PASS' if passed else 'FAIL'}")
    print(f"duplicate_primary_listing_rows={duplicate_rows}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
