"""Run yearly historical backfill using free sources."""
# pyright: reportMissingImports=false,reportMissingTypeStubs=false,reportUnknownVariableType=false,reportUnknownArgumentType=false,reportUnknownMemberType=false,reportCallIssue=false

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Callable, cast

import pandas as pd

from sector.collect import ft as collect_ft
from sector.collect import kr as collect_kr

DEFAULT_START_YEAR = 2010
DEFAULT_END_YEAR = 2026
DEFAULT_MONTH_DAY = "12-31"
DEFAULT_HISTORY_OUTPUT = Path("data/normalized/sector_history_pit_yearly.parquet")
DEFAULT_CURRENT_ROOT = Path("data/model_ready/yearly")
DEFAULT_KR_PROXY_ROOT = Path("data/normalized/yearly/kr_icb_proxy")
DEFAULT_SUMMARY_OUTPUT = Path("data/normalized/yearly_backfill_summary.json")
DEFAULT_RAW_FT_ROOT = Path("data/raw/ft")
DEFAULT_RAW_KR_ROOT = Path("data/raw/kr")


@dataclass(frozen=True)
class BackfillOptions:
    start_year: int
    end_year: int
    month_day: str
    history_output: Path
    current_root: Path
    kr_proxy_root: Path
    summary_output: Path


def _run_step(name: str, argv: list[str], runner: Callable[[list[str]], int]) -> None:
    print(f"step={name}")
    print("cmd=" + " ".join(argv))
    code = int(runner(argv))
    if code != 0:
        raise RuntimeError(f"{name} failed with exit_code={code}")


def _build_as_of(year: int, month_day: str) -> str:
    token = month_day.strip()
    parts = token.split("-")
    if len(parts) != 2:
        raise ValueError("--month-day must be MM-DD")
    month = int(parts[0])
    day = int(parts[1])
    value = date(year, month, day)
    return value.isoformat()


def _kr_indices_for_year(year: int) -> str:
    if year < 2015:
        return "kospi200"
    return "kospi200,kosdaq150"


def _check_history_order(history_output: Path, first_as_of: str) -> None:
    if not history_output.exists():
        return
    existing = pd.read_parquet(history_output)
    if "effective_from" not in existing.columns or existing.empty:
        return
    max_existing = str(cast(pd.Series, existing["effective_from"]).astype(str).max())
    if first_as_of < max_existing:
        raise RuntimeError(
            f"history-output contains later dates than requested backfill start. "
            + f"start_as_of={first_as_of}, existing_max_effective_from={max_existing}, use a fresh --history-output path"
        )


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _ = path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _read_parquet_required(path: Path, required_columns: set[str]) -> pd.DataFrame:
    if not path.exists():
        raise RuntimeError(f"required parquet not found: {path.as_posix()}")
    frame = pd.read_parquet(path)
    missing = sorted(required_columns.difference(frame.columns))
    if missing:
        raise RuntimeError(
            f"parquet missing required columns at {path.as_posix()}: {','.join(missing)}"
        )
    return frame


def _normalize_ft_rows(frame: pd.DataFrame, country: str, as_of: str) -> pd.DataFrame:
    base = frame.copy()
    base["stock_code"] = cast(pd.Series, base["stock_code"]).astype(str).str.strip().str.upper()
    base["stock_name"] = cast(pd.Series, base["stock_name"]).astype(str).str.strip()
    exchange_series = cast(pd.Series, base.get("exchange_code", "")).astype(str).str.strip().str.upper()
    level1_series = cast(pd.Series, base.get("native_taxonomy_level1", "")).astype(str).str.strip()
    level3_series = cast(pd.Series, base.get("native_taxonomy_label", "")).astype(str).str.strip()
    ticker_series = cast(pd.Series, base.get("ticker", "")).astype(str).str.strip()
    source_series = cast(pd.Series, base.get("tearsheet_url", "")).astype(str).str.strip()

    normalized = pd.DataFrame(
        {
            "security_id": country.upper()
            + ":"
            + cast(pd.Series, base["stock_code"]).astype(str)
            + ":"
            + exchange_series,
            "ticker": ticker_series,
            "country": country,
            "stock_code": cast(pd.Series, base["stock_code"]).astype(str),
            "stock_name": cast(pd.Series, base["stock_name"]).astype(str),
            "native_taxonomy_label": level3_series,
            "icb_proxy_level1": level1_series,
            "icb_proxy_level3": level3_series,
            "as_of_date": as_of,
            "source_tag": source_series,
        }
    )
    normalized = normalized[normalized["stock_code"].astype(str) != ""]
    return cast(pd.DataFrame, normalized)


def _normalize_kr_rows(frame: pd.DataFrame, as_of: str) -> pd.DataFrame:
    base = frame.copy()
    base["stock_code"] = cast(pd.Series, base["stock_code"]).astype(str).str.strip().str.zfill(6)
    base["stock_name"] = cast(pd.Series, base["stock_name"]).astype(str).str.strip()
    level3_series = cast(pd.Series, base.get("native_taxonomy_label", "")).astype(str).str.strip()
    source_series = cast(pd.Series, base.get("taxonomy_source", "")).astype(str).str.strip()
    index_series = cast(pd.Series, base.get("index_name", "")).astype(str).str.strip().str.upper()
    level1_series = index_series.replace(
        {
            "KOSPI200": "Large Cap",
            "KOSDAQ150": "Mid Cap",
            "": "ICB_UNKNOWN",
        }
    )

    normalized = pd.DataFrame(
        {
            "security_id": "KR:" + cast(pd.Series, base["stock_code"]).astype(str),
            "ticker": cast(pd.Series, base["stock_code"]).astype(str) + ":KRX",
            "country": "kr",
            "stock_code": cast(pd.Series, base["stock_code"]).astype(str),
            "stock_name": cast(pd.Series, base["stock_name"]).astype(str),
            "native_taxonomy_label": level3_series,
            "icb_proxy_level1": cast(pd.Series, level1_series).astype(str),
            "icb_proxy_level3": level3_series,
            "as_of_date": as_of,
            "source_tag": source_series,
        }
    )
    normalized = normalized[normalized["stock_code"].astype(str) != ""]
    normalized = normalized.drop_duplicates(subset=["security_id"], keep="first").reset_index(drop=True)
    return normalized


def _build_current_snapshot(as_of: str) -> pd.DataFrame:
    us_path = DEFAULT_RAW_FT_ROOT / as_of / "us" / "constituents.parquet"
    jp_path = DEFAULT_RAW_FT_ROOT / as_of / "jp" / "constituents.parquet"
    kr_path = DEFAULT_RAW_KR_ROOT / as_of / "constituents.parquet"

    us_raw = _read_parquet_required(us_path, {"stock_code", "stock_name", "ticker"})
    jp_raw = _read_parquet_required(jp_path, {"stock_code", "stock_name", "ticker"})
    kr_raw = _read_parquet_required(kr_path, {"stock_code", "stock_name", "native_taxonomy_label"})

    us_norm = _normalize_ft_rows(us_raw, "us", as_of)
    jp_norm = _normalize_ft_rows(jp_raw, "jp", as_of)
    kr_norm = _normalize_kr_rows(kr_raw, as_of)

    combined = pd.concat([us_norm, jp_norm, kr_norm], ignore_index=True)
    combined = combined.sort_values(by=["country", "security_id"])
    combined = combined.drop_duplicates(subset=["security_id"], keep="first").reset_index(drop=True)
    return combined


def _update_history(history_output: Path, current: pd.DataFrame, as_of: str) -> pd.DataFrame:
    required = {
        "security_id",
        "ticker",
        "country",
        "stock_code",
        "stock_name",
        "native_taxonomy_label",
        "icb_proxy_level1",
        "icb_proxy_level3",
        "as_of_date",
        "source_tag",
    }
    missing_current = sorted(required.difference(current.columns))
    if missing_current:
        raise RuntimeError("current snapshot missing columns: " + ",".join(missing_current))

    if history_output.exists():
        existing = pd.read_parquet(history_output)
    else:
        empty_columns = list(required) + ["effective_from", "effective_to", "is_current"]
        existing = pd.DataFrame({column: pd.Series(dtype="object") for column in empty_columns})

    existing = existing.copy()
    if not existing.empty:
        existing = existing[existing["effective_from"].astype(str) != as_of].copy()
        close_mask = (existing["is_current"].astype(bool)) & (existing["effective_from"].astype(str) < as_of)
        existing.loc[close_mask, "effective_to"] = as_of
        existing.loc[close_mask, "is_current"] = False

    incoming = current.copy()
    incoming["effective_from"] = as_of
    incoming["effective_to"] = None
    incoming["is_current"] = True

    history = pd.concat([existing, incoming], ignore_index=True)
    history = history.drop_duplicates(subset=["security_id", "effective_from"], keep="last").reset_index(drop=True)

    history_output.parent.mkdir(parents=True, exist_ok=True)
    _ = history.to_parquet(history_output, index=False)
    return history


def _write_country_csvs(current: pd.DataFrame, outdir: Path) -> dict[str, str]:
    outdir.mkdir(parents=True, exist_ok=True)
    outputs: dict[str, str] = {}
    for country in ("us", "kr", "jp"):
        country_rows = current[current["country"].astype(str) == country].copy()
        country_path = outdir / f"{country}_sector_current.csv"
        _ = country_rows.to_csv(country_path, index=False)
        outputs[country] = country_path.as_posix()
    return outputs


def run_backfill(options: BackfillOptions) -> dict[str, object]:
    years = list(range(options.start_year, options.end_year + 1))
    if not years:
        raise RuntimeError("no target years to backfill")
    first_as_of = _build_as_of(years[0], options.month_day)
    _check_history_order(options.history_output, first_as_of)

    runs: list[dict[str, object]] = []
    for year in years:
        as_of = _build_as_of(year, options.month_day)
        kr_indices = _kr_indices_for_year(year)
        current_output = options.current_root / as_of / "sector_current_primary.parquet"
        csv_outdir = options.current_root / as_of / "csv"
        current_output = options.current_root / as_of / "sector_current_primary.parquet"
        csv_outdir = options.current_root / as_of / "csv"

        _run_step(
            "collect.ft.us",
            [
                "--market",
                "us",
                "--as-of",
                as_of,
                "--parser",
                "js-engine",
                "--universe-scope",
                "russell3000",
            ],
            collect_ft.main,
        )
        _run_step(
            "collect.ft.jp",
            [
                "--market",
                "jp",
                "--as-of",
                as_of,
                "--parser",
                "js-engine",
                "--universe-scope",
                "topix500_proxy",
            ],
            collect_ft.main,
        )
        _run_step(
            "collect.kr",
            [
                "--indices",
                kr_indices,
                "--as-of",
                as_of,
                "--no-local-fallback",
            ],
            collect_kr.main,
        )

        current = _build_current_snapshot(as_of)
        current_output.parent.mkdir(parents=True, exist_ok=True)
        current.to_parquet(current_output, index=False)
        history = _update_history(options.history_output, current, as_of)
        csv_outputs = _write_country_csvs(current, csv_outdir)

        country_counts = {
            "us": int((current["country"].astype(str) == "us").sum()),
            "kr": int((current["country"].astype(str) == "kr").sum()),
            "jp": int((current["country"].astype(str) == "jp").sum()),
        }
        runs.append(
            {
                "year": year,
                "as_of": as_of,
                "kr_indices": kr_indices,
                "current_output": current_output.as_posix(),
                "csv_outdir": csv_outdir.as_posix(),
                "csv_outputs": csv_outputs,
                "history_rows": int(len(history)),
                "current_rows": int(len(current)),
                "country_counts": country_counts,
            }
        )

    summary: dict[str, object] = {
        "result": "PASS",
        "start_year": options.start_year,
        "end_year": options.end_year,
        "month_day": options.month_day,
        "history_output": options.history_output.as_posix(),
        "runs": runs,
    }
    _write_json(options.summary_output, summary)
    return summary


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run yearly historical sector backfill")
    _ = parser.add_argument("--start-year", type=int, default=DEFAULT_START_YEAR)
    _ = parser.add_argument("--end-year", type=int, default=DEFAULT_END_YEAR)
    _ = parser.add_argument("--month-day", default=DEFAULT_MONTH_DAY)
    _ = parser.add_argument("--history-output", default=DEFAULT_HISTORY_OUTPUT.as_posix())
    _ = parser.add_argument("--current-root", default=DEFAULT_CURRENT_ROOT.as_posix())
    _ = parser.add_argument("--kr-proxy-root", default=DEFAULT_KR_PROXY_ROOT.as_posix())
    _ = parser.add_argument("--summary-output", default=DEFAULT_SUMMARY_OUTPUT.as_posix())
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    start_year = cast(int, args.start_year)
    end_year = cast(int, args.end_year)
    if start_year > end_year:
        parser.error("--start-year must be <= --end-year")

    try:
        _ = _build_as_of(start_year, cast(str, args.month_day))
    except ValueError as exc:
        parser.error(str(exc))

    options = BackfillOptions(
        start_year=start_year,
        end_year=end_year,
        month_day=cast(str, args.month_day),
        history_output=Path(cast(str, args.history_output)),
        current_root=Path(cast(str, args.current_root)),
        kr_proxy_root=Path(cast(str, args.kr_proxy_root)),
        summary_output=Path(cast(str, args.summary_output)),
    )

    try:
        summary = run_backfill(options)
    except (RuntimeError, ValueError) as exc:
        print("result=FAIL")
        print(f"error={exc}")
        return 1

    print("result=PASS")
    print(f"start_year={summary['start_year']}")
    print(f"end_year={summary['end_year']}")
    print(f"history_output={summary['history_output']}")
    print(f"summary_output={options.summary_output.as_posix()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
