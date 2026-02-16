"""Compare deterministic content hash across two tabular snapshots."""
# pyright: reportMissingTypeStubs=false,reportUnknownMemberType=false,reportUnknownVariableType=false,reportUnknownArgumentType=false,reportAttributeAccessIssue=false,reportCallIssue=false

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import cast

import pandas as pd


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="QA: compare deterministic hashes")
    _ = parser.add_argument("--left", required=True)
    _ = parser.add_argument("--right", required=True)
    return parser


def _load(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise RuntimeError(f"input file does not exist: {path.as_posix()}")
    if path.suffix.lower() == ".csv":
        return pd.read_csv(path)
    return pd.read_parquet(path)


def _normalize_value(value: object) -> str:
    if bool(pd.isna(value)):
        return "<NA>"
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _content_hash(frame: pd.DataFrame) -> str:
    columns = sorted([str(name) for name in frame.columns])
    canonical = frame.loc[:, columns].copy()
    for column in columns:
        canonical[column] = canonical[column].apply(_normalize_value)
    canonical = canonical.sort_values(by=columns, kind="stable").reset_index(drop=True)

    digest = hashlib.sha256()
    for row in canonical.itertuples(index=False, name=None):
        payload = {columns[idx]: cast(str, value) for idx, value in enumerate(row)}
        line = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        digest.update(line.encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    left_path = Path(cast(str, args.left))
    right_path = Path(cast(str, args.right))

    try:
        left = _load(left_path)
        right = _load(right_path)
    except RuntimeError as exc:
        print("result=FAIL")
        print(f"error={exc}")
        return 1

    left_columns = sorted([str(name) for name in left.columns])
    right_columns = sorted([str(name) for name in right.columns])
    if left_columns != right_columns:
        print("result=FAIL")
        print("hash_equal=false")
        print("error=schema mismatch")
        return 1

    left_hash = _content_hash(left)
    right_hash = _content_hash(right)
    equal = left_hash == right_hash
    print("result=PASS" if equal else "result=FAIL")
    print(f"hash_equal={'true' if equal else 'false'}")
    print(f"left_hash={left_hash}")
    print(f"right_hash={right_hash}")
    return 0 if equal else 1


if __name__ == "__main__":
    raise SystemExit(main())
