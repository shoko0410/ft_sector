"""Build unified sector PIT history and current primary snapshot."""
# pyright: reportMissingTypeStubs=false,reportUnknownMemberType=false,reportUnknownVariableType=false,reportUnknownArgumentType=false,reportUnnecessaryCast=false,reportUnknownLambdaType=false,reportCallIssue=false

from __future__ import annotations

import argparse
import json
import re
from datetime import date
from pathlib import Path
from typing import cast

import pandas as pd

DEFAULT_AS_OF = "2026-01-31"
DEFAULT_HISTORY_OUTPUT = Path("data/normalized/sector_history_pit.parquet")
DEFAULT_CURRENT_OUTPUT = Path("data/model_ready/sector_current_primary.parquet")
DEFAULT_KR_PROXY_INPUT = Path("data/normalized/kr_icb_proxy_crosswalk.parquet")

FT_SYMBOL_ICB: dict[str, tuple[str, str]] = {
    "AAPL:NSQ": ("Technology", "Technology Hardware and Equipment"),
    "7203:TYO": ("Consumer Discretionary", "Automobiles and Parts"),
}


FT_ICB_L3_TO_L1: dict[str, str] = {
    "Aerospace and Defense": "Industrials",
    "Automobiles and Parts": "Consumer Discretionary",
    "Banks": "Financials",
    "Basic Resources": "Basic Materials",
    "Beverages": "Consumer Staples",
    "Chemicals": "Basic Materials",
    "Construction and Materials": "Industrials",
    "Consumer Services": "Consumer Discretionary",
    "Electronic and Electrical Equipment": "Industrials",
    "Electricity": "Utilities",
    "Finance and Credit Services": "Financials",
    "Financial Services": "Financials",
    "Food Producers": "Consumer Staples",
    "Forestry and Paper": "Basic Materials",
    "Gas, Water and Multiutilities": "Utilities",
    "General Industrials": "Industrials",
    "Health Care Providers": "Health Care",
    "Household Goods and Home Construction": "Consumer Discretionary",
    "Industrial Engineering": "Industrials",
    "Industrial Materials": "Industrials",
    "Industrial Support Services": "Industrials",
    "Industrial Transportation": "Industrials",
    "Insurance": "Financials",
    "Investment Banking and Brokerage Services": "Financials",
    "Leisure Goods": "Consumer Discretionary",
    "Media": "Consumer Discretionary",
    "Medical Equipment and Services": "Health Care",
    "Oil, Gas and Coal": "Energy",
    "Personal Care, Drug and Grocery Stores": "Consumer Staples",
    "Personal Goods": "Consumer Discretionary",
    "Pharmaceuticals and Biotechnology": "Health Care",
    "Precious Metals and Mining": "Basic Materials",
    "Real Estate Investment and Services": "Real Estate",
    "Retailers": "Consumer Discretionary",
    "Software and Computer Services": "Technology",
    "Technology Hardware and Equipment": "Technology",
    "Telecommunications Equipment": "Technology",
    "Telecommunications Service Providers": "Telecommunications",
    "Travel and Leisure": "Consumer Discretionary",
    "Waste and Disposal Services": "Industrials",
}


FT_L1_KEYWORDS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("bank", "financial", "insurance", "broker"), "Financials"),
    (("pharma", "biotech", "medical", "health"), "Health Care"),
    (("software", "computer", "semiconductor", "technology"), "Technology"),
    (("telecommunication",), "Telecommunications"),
    (("oil", "gas", "coal", "energy"), "Energy"),
    (("real estate",), "Real Estate"),
    (("utility", "water"), "Utilities"),
    (("chemical", "resource", "metals", "mining", "paper"), "Basic Materials"),
    (("food", "beverage", "grocery", "tobacco"), "Consumer Staples"),
    (("automobile", "retail", "media", "travel", "leisure", "personal goods", "household", "consumer"), "Consumer Discretionary"),
    (("industrial", "construction", "engineering", "transportation", "aerospace"), "Industrials"),
)

FT_DEFAULT_L3_BY_L1: dict[str, str] = {
    "Basic Materials": "Basic Resources",
    "Consumer Discretionary": "Consumer Services",
    "Consumer Staples": "Food Producers",
    "Energy": "Oil, Gas and Coal",
    "Financials": "Financial Services",
    "Health Care": "Pharmaceuticals and Biotechnology",
    "Industrials": "General Industrials",
    "Real Estate": "Real Estate Investment and Services",
    "Technology": "Technology Hardware and Equipment",
    "Telecommunications": "Telecommunications Service Providers",
    "Utilities": "Electricity",
}

EXCHANGE_MAP = {
    "NSQ": "NASDAQ",
    "NYS": "NYSE",
    "TYO": "TSE",
}

def _deterministic_source_ts(as_of: str) -> str:
    return f"{as_of}T00:00:00+00:00"


def _validate_as_of(value: str) -> str:
    try:
        _ = date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("--as-of must be in YYYY-MM-DD format") from exc
    return value


def _read_json(path: Path) -> dict[str, object]:
    if not path.exists():
        raise RuntimeError(f"missing file: {path.as_posix()}")
    obj = cast(object, json.loads(path.read_text(encoding="utf-8")))
    if not isinstance(obj, dict):
        raise RuntimeError(f"expected JSON object: {path.as_posix()}")
    return cast(dict[str, object], obj)


def _extract_symbol(tearsheet_url: str) -> str:
    match = re.search(r"[?&]s=([^&]+)", tearsheet_url)
    if not match:
        raise RuntimeError(f"could not parse symbol from tearsheet_url={tearsheet_url}")
    return match.group(1)


def _extract_company_name(tearsheet_html: str) -> str:
    name_match = re.search(
        r"mod-tearsheet-overview__header__name[^>]*>([^<]+)<",
        tearsheet_html,
        flags=re.IGNORECASE,
    )
    if name_match:
        return name_match.group(1).strip()

    title_match = re.search(r"<title>([^,]+),", tearsheet_html, flags=re.IGNORECASE)
    if title_match:
        return title_match.group(1).strip()

    raise RuntimeError("could not extract stock_name from tearsheet html")


def _extract_ft_exchange_code(ticker: str) -> str:
    parts = [part.strip() for part in ticker.split(":") if part.strip()]
    if len(parts) < 2:
        return ""
    return parts[1]


def _map_ft_level1(industry_label: str) -> str:
    value = industry_label.strip()
    if value in FT_ICB_L3_TO_L1:
        return FT_ICB_L3_TO_L1[value]

    lowered = value.lower()
    for keywords, level1 in FT_L1_KEYWORDS:
        if any(keyword in lowered for keyword in keywords):
            return level1
    return "ICB_UNKNOWN"


def _map_ft_confidence(level1: str, level3: str, *, level3_is_fallback: bool = False) -> tuple[float, str]:
    if level1 == "ICB_UNKNOWN" or level3 == "ICB_L3_UNCERTAIN":
        return 0.40, "low"
    if level3_is_fallback:
        return 0.65, "medium"
    return 0.95, "high"


def _resolve_ft_taxonomy(*, level1_raw: str, level3_raw: str) -> tuple[str, str, bool]:
    level1_seed = level1_raw.strip()
    level3_seed = level3_raw.strip()

    level3_is_fallback = False
    if level3_seed and level3_seed != "--":
        level3 = level3_seed
    elif level1_seed and level1_seed in FT_DEFAULT_L3_BY_L1:
        level3 = FT_DEFAULT_L3_BY_L1[level1_seed]
        level3_is_fallback = True
    else:
        level3 = "ICB_L3_UNCERTAIN"

    if level1_seed and level1_seed != "--":
        level1 = level1_seed
    else:
        level1_source = level3 if level3 != "ICB_L3_UNCERTAIN" else level3_seed
        level1 = _map_ft_level1(level1_source)

    return level1, level3, level3_is_fallback


def _load_ft_market_rows(as_of: str, market: str) -> list[dict[str, object]]:
    market_dir = Path("data/raw/ft") / as_of / market
    constituents_path = market_dir / "constituents.parquet"
    metadata = _read_json(market_dir / "run_metadata.json")

    if constituents_path.exists():
        frame = cast(pd.DataFrame, pd.read_parquet(constituents_path))
        required = {"ticker", "stock_code", "stock_name", "native_taxonomy_label", "tearsheet_url"}
        missing = sorted(required.difference(frame.columns))
        if missing:
            raise RuntimeError("ft constituents missing required columns: " + ",".join(missing))

        if frame.empty:
            raise RuntimeError(f"ft constituents empty: {constituents_path.as_posix()}")

        universe = (
            str(frame.iloc[0]["universe_scope"])
            if "universe_scope" in frame.columns and str(frame.iloc[0]["universe_scope"]).strip()
            else ("russell3000" if market == "us" else "topix500")
        )
        source_ts = _deterministic_source_ts(as_of)
        run_id = f"ft-{market}-{as_of}"

        rows: list[dict[str, object]] = []
        for row in cast(list[dict[str, object]], frame.to_dict(orient="records")):
            ticker = str(row["ticker"]).strip()
            stock_code = str(row["stock_code"]).strip()
            stock_name = str(row["stock_name"]).strip()
            level1_raw = str(row.get("native_taxonomy_level1", "")).strip()
            level3_raw = str(row["native_taxonomy_label"]).strip()
            level1, level3, level3_is_fallback = _resolve_ft_taxonomy(level1_raw=level1_raw, level3_raw=level3_raw)
            confidence_score, confidence_band = _map_ft_confidence(
                level1=level1,
                level3=level3,
                level3_is_fallback=level3_is_fallback,
            )
            mapping_method = "ft_index_membership_filtered_level3_fallback" if level3_is_fallback else "ft_index_membership_filtered"

            exchange_code = _extract_ft_exchange_code(ticker)
            if not exchange_code:
                continue
            security_id = f"{market.upper()}:{ticker}"
            issuer_id = f"{market.upper()}:{stock_code}"
            exchange = EXCHANGE_MAP.get(exchange_code, exchange_code)

            rows.append(
                {
                    "as_of_date": as_of,
                    "security_id": security_id,
                    "issuer_id": issuer_id,
                    "ticker": ticker,
                    "stock_code": stock_code,
                    "stock_name": stock_name,
                    "exchange": exchange,
                    "country": market,
                    "universe": universe,
                    "native_taxonomy_label": level3,
                    "taxonomy_source": "ft_icb_native",
                    "taxonomy_version": as_of,
                    "icb_proxy_level1": level1,
                    "icb_proxy_level3": level3,
                    "mapping_version": "ft_native_v2",
                    "mapping_method": mapping_method,
                    "confidence_score": confidence_score,
                    "confidence_band": confidence_band,
                    "mapping_effective_date": as_of,
                    "source_url": str(row["tearsheet_url"]),
                    "source_ts": source_ts,
                    "run_id": run_id,
                }
            )

        if rows:
            return rows

    tearsheet_html_path = market_dir / "tearsheet_sample.html"
    if not tearsheet_html_path.exists():
        raise RuntimeError(f"missing file: {tearsheet_html_path.as_posix()}")
    tearsheet_html = tearsheet_html_path.read_text(encoding="utf-8")

    tearsheet_url = str(metadata.get("tearsheet_url", ""))
    if not tearsheet_url:
        raise RuntimeError(f"missing tearsheet_url in {market_dir.as_posix()}/run_metadata.json")

    symbol = _extract_symbol(tearsheet_url)
    stock_name = _extract_company_name(tearsheet_html)
    stock_code, exchange_code = symbol.split(":", maxsplit=1)
    icb_l1, icb_l3 = FT_SYMBOL_ICB.get(symbol, ("ICB_UNKNOWN", "ICB_L3_UNCERTAIN"))

    security_id = f"{market.upper()}:{symbol}"
    issuer_id = f"{market.upper()}:{stock_code}"
    exchange = EXCHANGE_MAP.get(exchange_code, exchange_code)
    universe = "russell3000" if market == "us" else "topix500"
    source_ts = _deterministic_source_ts(as_of)
    run_id = f"ft-{market}-{as_of}"

    return [
        {
            "as_of_date": as_of,
            "security_id": security_id,
            "issuer_id": issuer_id,
            "ticker": symbol,
            "stock_code": stock_code,
            "stock_name": stock_name,
            "exchange": exchange,
            "country": market,
            "universe": universe,
            "native_taxonomy_label": icb_l3,
            "taxonomy_source": "ft_icb_native",
            "taxonomy_version": as_of,
            "icb_proxy_level1": icb_l1,
            "icb_proxy_level3": icb_l3,
            "mapping_version": "ft_native_v1",
            "mapping_method": "ft_tearsheet_symbol_seed",
            "confidence_score": 1.0,
            "confidence_band": "high",
            "mapping_effective_date": as_of,
            "source_url": tearsheet_url,
            "source_ts": source_ts,
            "run_id": run_id,
        }
    ]


def _load_kr_rows(as_of: str, kr_proxy_input: Path) -> list[dict[str, object]]:
    if not kr_proxy_input.exists():
        raise RuntimeError(f"input parquet does not exist: {kr_proxy_input.as_posix()}")
    proxy = cast(pd.DataFrame, pd.read_parquet(kr_proxy_input))
    required_proxy = {
        "as_of_date",
        "stock_code",
        "stock_name",
        "native_taxonomy_label",
        "taxonomy_source",
        "taxonomy_version",
        "icb_proxy_level1",
        "icb_proxy_level3",
        "mapping_version",
        "mapping_method",
        "confidence_score",
        "confidence_band",
        "mapping_effective_date",
    }
    missing_proxy = sorted(required_proxy.difference(proxy.columns))
    if missing_proxy:
        raise RuntimeError("kr proxy input missing columns: " + ",".join(missing_proxy))

    constituents_path = Path(f"data/raw/kr/{as_of}/constituents.parquet")
    if not constituents_path.exists():
        raise RuntimeError(f"input parquet does not exist: {constituents_path.as_posix()}")
    constituents = cast(pd.DataFrame, pd.read_parquet(constituents_path))
    if "stock_code" not in constituents.columns or "index_name" not in constituents.columns:
        raise RuntimeError("kr constituents input missing stock_code/index_name columns")

    universe_map = (
        constituents.assign(stock_code=constituents["stock_code"].astype(str).str.zfill(6))
        .groupby("stock_code")["index_name"]
        .apply(lambda values: ",".join(sorted({str(item) for item in cast(pd.Series, values).to_list()})))
        .to_dict()
    )

    proxy_rows: list[dict[str, object]] = []
    for row in cast(list[dict[str, object]], proxy.to_dict(orient="records")):
        stock_code = str(row["stock_code"]).zfill(6)
        security_id = f"KRX:{stock_code}"
        proxy_rows.append(
            {
                "as_of_date": str(row["as_of_date"]),
                "security_id": security_id,
                "issuer_id": security_id,
                "ticker": stock_code,
                "stock_code": stock_code,
                "stock_name": str(row["stock_name"]),
                "exchange": "KRX",
                "country": "kr",
                "universe": universe_map.get(stock_code, "kospi200,kosdaq150"),
                "native_taxonomy_label": str(row["native_taxonomy_label"]),
                "taxonomy_source": str(row["taxonomy_source"]),
                "taxonomy_version": str(row["taxonomy_version"]),
                "icb_proxy_level1": str(row["icb_proxy_level1"]),
                "icb_proxy_level3": str(row["icb_proxy_level3"]),
                "mapping_version": str(row["mapping_version"]),
                "mapping_method": str(row["mapping_method"]),
                "confidence_score": float(cast(float, row["confidence_score"])),
                "confidence_band": str(row["confidence_band"]),
                "mapping_effective_date": str(row["mapping_effective_date"]),
                "source_url": "data/raw/kr/{as_of}/constituents.parquet".format(as_of=as_of),
                "source_ts": _deterministic_source_ts(as_of),
                "run_id": f"kr-proxy-{as_of}",
            }
        )
    return proxy_rows


def _build_incremental_history(existing: pd.DataFrame, incoming: pd.DataFrame, as_of: str) -> pd.DataFrame:
    if existing.empty:
        updated_existing = existing.copy()
    else:
        updated_existing = existing[existing["effective_from"].astype(str) != as_of].copy()

        current_mask = (
            (updated_existing["is_current"].astype(bool))
            & (updated_existing["effective_from"].astype(str) < as_of)
        )
        updated_existing.loc[current_mask, "effective_to"] = as_of
        updated_existing.loc[current_mask, "is_current"] = False

    combined = pd.concat([updated_existing, incoming], ignore_index=True)
    combined = combined.sort_values(["security_id", "effective_from"], kind="stable").reset_index(drop=True)
    return combined


def _primary_listing_rank(row: pd.Series) -> int:
    country = str(row["country"]).lower()
    exchange = str(row["exchange"]).upper()
    if country == "us" and exchange in {"NASDAQ", "NYSE"}:
        return 1
    if country == "jp" and exchange == "TSE":
        return 1
    if country == "kr" and exchange == "KRX":
        return 1
    return 2


def _write_parquet(path: Path, frame: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        frame.to_parquet(path, index=False)
    except ImportError as exc:
        raise RuntimeError("parquet engine missing. Install pyarrow or fastparquet") from exc


def build_pit(as_of: str, history_output: Path, current_output: Path, kr_proxy_input: Path) -> dict[str, object]:
    us_rows = _load_ft_market_rows(as_of=as_of, market="us")
    jp_rows = _load_ft_market_rows(as_of=as_of, market="jp")
    kr_rows = _load_kr_rows(as_of=as_of, kr_proxy_input=kr_proxy_input)
    incoming = pd.DataFrame(us_rows + jp_rows + kr_rows)
    incoming["effective_from"] = as_of
    incoming["effective_to"] = None
    incoming["is_current"] = True

    leakage_mask = incoming["mapping_effective_date"].astype(str) > incoming["as_of_date"].astype(str)
    future_mapping_rows = int(leakage_mask.sum())
    if future_mapping_rows > 0:
        raise RuntimeError(f"proxy PIT leakage detected: future_mapping_rows={future_mapping_rows}")

    if history_output.exists():
        existing = cast(pd.DataFrame, pd.read_parquet(history_output))
    else:
        existing = pd.DataFrame(columns=incoming.columns)

    history = _build_incremental_history(existing=existing, incoming=incoming, as_of=as_of)
    _write_parquet(history_output, history)

    current = history[history["is_current"].astype(bool)].copy()
    current["primary_listing_rank"] = current.apply(_primary_listing_rank, axis=1)
    current = (
        current.sort_values(["issuer_id", "primary_listing_rank", "security_id"], kind="stable")
        .drop_duplicates(subset=["issuer_id"], keep="first")
        .drop(columns=["primary_listing_rank"])
        .reset_index(drop=True)
    )
    _write_parquet(current_output, current)

    return {
        "pit_rows": int(len(history)),
        "current_rows": int(len(current)),
        "future_mapping_rows": future_mapping_rows,
        "countries": sorted(set(cast(list[str], current["country"].astype(str).tolist()))),
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build sector PIT and current primary snapshot")
    _ = parser.add_argument("--as-of", default=DEFAULT_AS_OF)
    _ = parser.add_argument("--history-output", default=DEFAULT_HISTORY_OUTPUT.as_posix())
    _ = parser.add_argument("--current-output", default=DEFAULT_CURRENT_OUTPUT.as_posix())
    _ = parser.add_argument("--kr-proxy-input", default=DEFAULT_KR_PROXY_INPUT.as_posix())
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        as_of = _validate_as_of(cast(str, args.as_of))
    except ValueError as exc:
        parser.error(str(exc))

    history_output = Path(cast(str, args.history_output))
    current_output = Path(cast(str, args.current_output))
    kr_proxy_input = Path(cast(str, args.kr_proxy_input))

    try:
        stats = build_pit(
            as_of=as_of,
            history_output=history_output,
            current_output=current_output,
            kr_proxy_input=kr_proxy_input,
        )
    except RuntimeError as exc:
        print("result=FAIL")
        print(f"error={exc}")
        return 1

    print("result=PASS")
    print(f"as_of={as_of}")
    print(f"history_output={history_output.as_posix()}")
    print(f"current_output={current_output.as_posix()}")
    print(f"pit_rows={stats['pit_rows']}")
    print(f"current_rows={stats['current_rows']}")
    print(f"future_mapping_rows={stats['future_mapping_rows']}")
    print("countries=" + ",".join(cast(list[str], stats["countries"])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
