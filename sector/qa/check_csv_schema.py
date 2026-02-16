"""Check required schema fields for exported CSV files."""
# pyright: reportMissingTypeStubs=false,reportUnknownMemberType=false,reportUnknownVariableType=false,reportUnknownArgumentType=false

from __future__ import annotations

import argparse
from pathlib import Path
from typing import cast

import pandas as pd


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="QA: CSV schema checker")
    _ = parser.add_argument("--inputs", required=True, help="comma-separated csv paths")
    _ = parser.add_argument("--required", required=True, help="comma-separated required columns")
    return parser


def _parse_csv_list(raw: str) -> list[Path]:
    parts = [item.strip() for item in raw.split(",") if item.strip()]
    if not parts:
        raise RuntimeError("--inputs must include at least one CSV path")
    return [Path(item) for item in parts]


def _parse_required(raw: str) -> list[str]:
    parts = [item.strip() for item in raw.split(",") if item.strip()]
    if not parts:
        raise RuntimeError("--required must include at least one column")
    return parts


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        paths = _parse_csv_list(cast(str, args.inputs))
        required = _parse_required(cast(str, args.required))
    except RuntimeError as exc:
        print("result=FAIL")
        print(f"error={exc}")
        return 1

    failures: list[str] = []
    for path in paths:
        if not path.exists():
            failures.append(f"missing file: {path.as_posix()}")
            continue
        frame = pd.read_csv(path)
        missing = [column for column in required if column not in frame.columns]
        if missing:
            failures.append(f"{path.as_posix()} missing columns: {','.join(missing)}")

    if failures:
        print("result=FAIL")
        print("error=" + " | ".join(failures))
        return 1

    print("result=PASS")
    print("checked_files=" + str(len(paths)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
