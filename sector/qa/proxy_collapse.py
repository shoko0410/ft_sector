"""Check KR proxy many-to-one collapse severity."""
# pyright: reportMissingTypeStubs=false,reportUnknownMemberType=false,reportUnknownVariableType=false,reportUnknownArgumentType=false,reportUnnecessaryCast=false

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import cast

import pandas as pd


def _load(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise RuntimeError(f"input parquet does not exist: {path.as_posix()}")
    return cast(pd.DataFrame, pd.read_parquet(path))


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="QA: proxy collapse")
    _ = parser.add_argument("--input", required=True)
    _ = parser.add_argument("--max-native-to-one-icb", type=int, default=8)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    frame = _load(Path(cast(str, args.input)))
    required = {"native_taxonomy_label", "icb_proxy_level3"}
    missing = sorted(required.difference(frame.columns))
    if missing:
        print("result=FAIL")
        print(f"error=missing columns: {','.join(missing)}")
        return 1

    if len(frame) == 0:
        print("result=FAIL")
        print("error=empty input")
        return 1

    collapse_series = frame.groupby("icb_proxy_level3")["native_taxonomy_label"].nunique(dropna=True)
    max_native_to_one_icb = int(collapse_series.max()) if len(collapse_series) > 0 else 0
    worst_bucket = str(collapse_series.idxmax()) if len(collapse_series) > 0 else ""
    threshold = cast(int, args.max_native_to_one_icb)
    passed = max_native_to_one_icb <= threshold

    payload = {
        "qa": "proxy_collapse",
        "status": "PASS" if passed else "FAIL",
        "max_native_to_one_icb": max_native_to_one_icb,
        "worst_bucket": worst_bucket,
        "threshold": threshold,
    }
    print(f"result={payload['status']}")
    print(json.dumps(payload, ensure_ascii=True, sort_keys=True))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
