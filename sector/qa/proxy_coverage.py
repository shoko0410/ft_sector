"""Check KR ICB proxy coverage and unknown/nonnull constraints."""
# pyright: reportMissingTypeStubs=false,reportUnknownMemberType=false,reportUnknownVariableType=false,reportUnknownArgumentType=false,reportUnnecessaryCast=false

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import cast

import pandas as pd

ICB_L1_UNKNOWN = "ICB_UNKNOWN"
ICB_L3_UNCERTAIN = "ICB_L3_UNCERTAIN"


def _load(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise RuntimeError(f"input parquet does not exist: {path.as_posix()}")
    return cast(pd.DataFrame, pd.read_parquet(path))


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="QA: proxy coverage")
    _ = parser.add_argument("--input", required=True)
    _ = parser.add_argument("--min-coverage", type=float, default=0.90)
    _ = parser.add_argument("--max-unknown", type=float, default=0.10)
    _ = parser.add_argument("--require-level3-nonnull", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    frame = _load(Path(cast(str, args.input)))
    required = {"icb_proxy_level1", "icb_proxy_level3"}
    missing = sorted(required.difference(frame.columns))
    if missing:
        print("result=FAIL")
        print(f"error=missing columns: {','.join(missing)}")
        return 1

    total = len(frame)
    if total == 0:
        print("result=FAIL")
        print("error=empty input")
        return 1

    nonnull_l3_ratio = float(frame["icb_proxy_level3"].notna().mean())
    coverage_ratio = float((frame["icb_proxy_level1"] != ICB_L1_UNKNOWN).mean())
    unknown_mask = (frame["icb_proxy_level1"] == ICB_L1_UNKNOWN) | (
        frame["icb_proxy_level3"] == ICB_L3_UNCERTAIN
    )
    unknown_ratio = float(unknown_mask.mean())

    ok_coverage = coverage_ratio >= cast(float, args.min_coverage)
    ok_unknown = unknown_ratio <= cast(float, args.max_unknown)
    require_l3 = cast(bool, args.require_level3_nonnull)
    ok_nonnull = (nonnull_l3_ratio == 1.0) if require_l3 else True
    passed = ok_coverage and ok_unknown and ok_nonnull

    payload = {
        "qa": "proxy_coverage",
        "status": "PASS" if passed else "FAIL",
        "total_rows": total,
        "coverage_ratio": round(coverage_ratio, 6),
        "unknown_ratio": round(unknown_ratio, 6),
        "nonnull_level3_ratio": round(nonnull_l3_ratio, 6),
        "thresholds": {
            "min_coverage": cast(float, args.min_coverage),
            "max_unknown": cast(float, args.max_unknown),
            "require_level3_nonnull": require_l3,
        },
    }

    print(f"result={payload['status']}")
    print(json.dumps(payload, ensure_ascii=True, sort_keys=True))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
