"""Check KR ICB proxy confidence distribution policy."""
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
    parser = argparse.ArgumentParser(description="QA: proxy confidence")
    _ = parser.add_argument("--input", required=True)
    _ = parser.add_argument("--min-high-share", type=float, default=0.70)
    _ = parser.add_argument("--max-low-share", type=float, default=0.15)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    frame = _load(Path(cast(str, args.input)))
    required = {"confidence_score", "confidence_band"}
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

    high_share = float((frame["confidence_band"] == "high").mean())
    low_share = float((frame["confidence_band"] == "low").mean())
    mean_score = float(frame["confidence_score"].astype(float).mean())

    passed = (high_share >= cast(float, args.min_high_share)) and (
        low_share <= cast(float, args.max_low_share)
    )

    payload = {
        "qa": "proxy_confidence",
        "status": "PASS" if passed else "FAIL",
        "total_rows": total,
        "high_share": round(high_share, 6),
        "low_share": round(low_share, 6),
        "mean_confidence_score": round(mean_score, 6),
        "thresholds": {
            "min_high_share": cast(float, args.min_high_share),
            "max_low_share": cast(float, args.max_low_share),
        },
    }

    print(f"result={payload['status']}")
    print(json.dumps(payload, ensure_ascii=True, sort_keys=True))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
