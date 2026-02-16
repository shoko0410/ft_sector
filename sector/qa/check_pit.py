"""Validate PIT overlap invariants."""
# pyright: reportMissingTypeStubs=false,reportUnknownMemberType=false,reportUnknownVariableType=false,reportUnknownArgumentType=false

from __future__ import annotations

import argparse
from pathlib import Path
from typing import cast

import pandas as pd


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="QA: PIT overlap check")
    _ = parser.add_argument("--input", required=True)
    _ = parser.add_argument("--key", required=True)
    _ = parser.add_argument("--from", dest="from_col", required=True)
    _ = parser.add_argument("--to", dest="to_col", required=True)
    return parser


def _load(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise RuntimeError(f"input parquet does not exist: {path.as_posix()}")
    return pd.read_parquet(path)


def _overlap_count(frame: pd.DataFrame, key: str, from_col: str, to_col: str) -> int:
    data = frame.copy()
    data[from_col] = pd.to_datetime(data[from_col], errors="coerce")
    data[to_col] = pd.to_datetime(data[to_col], errors="coerce")

    overlap_count = 0
    for _, group in data.sort_values([key, from_col], kind="stable").groupby(key):
        prev_to: pd.Timestamp | None = None
        for row in group.itertuples(index=False):
            current_from = cast(pd.Timestamp, getattr(row, from_col))
            current_to = cast(object, getattr(row, to_col))
            if bool(pd.isna(current_from)):
                continue
            if prev_to is not None and current_from < prev_to:
                overlap_count += 1
            if bool(pd.isna(current_to)):
                prev_to = pd.Timestamp.max
            else:
                prev_to = cast(pd.Timestamp, current_to)
    return overlap_count


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    path = Path(cast(str, args.input))
    key = cast(str, args.key)
    from_col = cast(str, args.from_col)
    to_col = cast(str, args.to_col)

    try:
        frame = _load(path)
    except RuntimeError as exc:
        print("result=FAIL")
        print(f"error={exc}")
        return 1

    required = {key, from_col, to_col}
    missing = sorted(required.difference(frame.columns))
    if missing:
        print("result=FAIL")
        print("error=missing columns: " + ",".join(missing))
        return 1

    overlap_count = _overlap_count(frame=frame, key=key, from_col=from_col, to_col=to_col)
    passed = overlap_count == 0
    print(f"result={'PASS' if passed else 'FAIL'}")
    print(f"overlap_count={overlap_count}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
