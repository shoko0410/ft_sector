"""Run end-to-end sector build orchestration for US/KR/JP."""
# pyright: reportMissingImports=false,reportUnknownVariableType=false,reportUnknownMemberType=false,reportUnknownArgumentType=false

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, cast

from sector.collect import ft as collect_ft
from sector.collect import kr as collect_kr
from sector.export import csv as export_csv
from sector.transform import build_pit
from sector.transform import kr_icb_proxy

DEFAULT_AS_OF = "2026-01-31"
DEFAULT_UNIVERSES = ("us:russell3000", "kr:kospi200,kosdaq150", "jp:topix500")
DEFAULT_KR_PROXY_LEVEL = "level1,level3"
DEFAULT_CURRENT_OUTPUT = Path("data/model_ready/sector_current_primary.parquet")
DEFAULT_HISTORY_OUTPUT = Path("data/normalized/sector_history_pit.parquet")
DEFAULT_KR_PROXY_OUTPUT = Path("data/normalized/kr_icb_proxy_crosswalk.parquet")
DEFAULT_KR_PROXY_METADATA = Path("data/normalized/kr_icb_proxy_crosswalk_run_metadata.json")
DEFAULT_CSV_OUTDIR = Path("data/model_ready/csv")


@dataclass(frozen=True)
class BuildOptions:
    as_of: str
    universes: dict[str, str]
    kr_proxy_level: str
    primary_listing_only: bool
    output: Path


def _parse_universes(raw: str | list[str]) -> dict[str, str]:
    tokens = raw if isinstance(raw, list) else raw.split()
    mapping: dict[str, str] = {}
    for token in tokens:
        piece = token.strip()
        if not piece:
            continue
        if ":" not in piece:
            raise ValueError(f"invalid universe token '{piece}' (expected market:scope)")
        market, scope = piece.split(":", maxsplit=1)
        key = market.strip().lower()
        value = scope.strip()
        if not key or not value:
            raise ValueError(f"invalid universe token '{piece}' (missing market/scope)")
        mapping[key] = value

    required = {"us", "kr", "jp"}
    missing = sorted(required.difference(mapping))
    if missing:
        raise ValueError("missing required universes: " + ",".join(missing))
    return mapping


def _run_step(name: str, argv: list[str], runner: Callable[[list[str]], int]) -> None:
    print(f"step={name}")
    print("cmd=" + " ".join(argv))
    code = int(runner(argv))
    if code != 0:
        raise RuntimeError(f"{name} failed with exit_code={code}")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run sector end-to-end build")
    _ = parser.add_argument("--as-of", default=DEFAULT_AS_OF)
    _ = parser.add_argument("--universes", nargs="+", default=list(DEFAULT_UNIVERSES))
    _ = parser.add_argument("--kr-proxy-level", default=DEFAULT_KR_PROXY_LEVEL)
    _ = parser.add_argument("--primary-listing-only", action="store_true")
    _ = parser.add_argument("--out", default=DEFAULT_CURRENT_OUTPUT.as_posix())
    return parser


def run_build(options: BuildOptions) -> None:
    kr_indices = options.universes["kr"]

    _run_step(
        "collect.ft.us",
        [
            "--market",
            "us",
            "--as-of",
            options.as_of,
            "--parser",
            "js-engine",
            "--universe-scope",
            options.universes["us"],
        ],
        collect_ft.main,
    )
    _run_step(
        "collect.ft.jp",
        [
            "--market",
            "jp",
            "--as-of",
            options.as_of,
            "--parser",
            "js-engine",
            "--universe-scope",
            options.universes["jp"],
        ],
        collect_ft.main,
    )
    _run_step(
        "collect.kr",
        ["--indices", kr_indices, "--as-of", options.as_of],
        collect_kr.main,
    )
    _run_step(
        "transform.kr_icb_proxy",
        [
            "--as-of",
            options.as_of,
            "--output",
            DEFAULT_KR_PROXY_OUTPUT.as_posix(),
            "--metadata",
            DEFAULT_KR_PROXY_METADATA.as_posix(),
        ],
        kr_icb_proxy.main,
    )
    _run_step(
        "transform.build_pit",
        [
            "--as-of",
            options.as_of,
            "--history-output",
            DEFAULT_HISTORY_OUTPUT.as_posix(),
            "--current-output",
            options.output.as_posix(),
            "--kr-proxy-input",
            DEFAULT_KR_PROXY_OUTPUT.as_posix(),
        ],
        build_pit.main,
    )
    _run_step(
        "export.csv",
        ["--input", options.output.as_posix(), "--outdir", DEFAULT_CSV_OUTDIR.as_posix()],
        export_csv.main,
    )


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        universes_raw = cast(list[str], args.universes)
        out_value = cast(str, args.out)
        as_of_value = cast(str, args.as_of)
        kr_proxy_level_value = cast(str, args.kr_proxy_level)
        primary_only_value = cast(bool, args.primary_listing_only)

        universes = _parse_universes(universes_raw)
        output = Path(out_value)
        options = BuildOptions(
            as_of=as_of_value,
            universes=universes,
            kr_proxy_level=kr_proxy_level_value,
            primary_listing_only=primary_only_value,
            output=output,
        )
        if options.kr_proxy_level.replace(" ", "") != "level1,level3":
            raise ValueError("--kr-proxy-level must be level1,level3")
        if not options.primary_listing_only:
            raise ValueError("--primary-listing-only is required for model-ready output contract")
        run_build(options)
    except (RuntimeError, ValueError) as exc:
        print("result=FAIL")
        print(f"error={exc}")
        return 1

    print("result=PASS")
    print(f"as_of={options.as_of}")
    print(f"output={options.output.as_posix()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
