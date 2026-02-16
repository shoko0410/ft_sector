"""Check KR proxy PIT leakage rule: mapping_effective_date <= as_of_date."""
# pyright: reportMissingTypeStubs=false,reportUnknownMemberType=false,reportUnknownVariableType=false,reportUnknownArgumentType=false

from __future__ import annotations

import argparse
from pathlib import Path
from typing import cast

import pandas as pd


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="QA: KR proxy PIT leakage")
    _ = parser.add_argument("--input", required=True)
    return parser


def _load(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise RuntimeError(f"input parquet does not exist: {path.as_posix()}")
    return pd.read_parquet(path)


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    path = Path(cast(str, args.input))

    try:
        frame = _load(path)
    except RuntimeError as exc:
        print("result=FAIL")
        print(f"error={exc}")
        return 1

    required = {"country", "mapping_effective_date", "as_of_date"}
    missing = sorted(required.difference(frame.columns))
    if missing:
        print("result=FAIL")
        print("error=missing columns: " + ",".join(missing))
        return 1

    kr = frame[frame["country"].astype(str).str.lower() == "kr"].copy()
    future_rows = int(
        (kr["mapping_effective_date"].astype(str) > kr["as_of_date"].astype(str)).sum()
    )
    passed = future_rows == 0
    print(f"result={'PASS' if passed else 'FAIL'}")
    print(f"future_mapping_rows={future_rows}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
