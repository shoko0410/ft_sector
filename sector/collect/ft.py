"""Collect FT US/JP raw pages with deterministic partitions."""
# pyright: reportMissingTypeStubs=false,reportUnknownMemberType=false,reportUnknownVariableType=false,reportUnknownArgumentType=false,reportUnnecessaryCast=false,reportAttributeAccessIssue=false,reportCallIssue=false,reportUnusedFunction=false

from __future__ import annotations

import argparse
import html
import io
import json
import re
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, date, timedelta
from http.client import HTTPResponse, IncompleteRead
from pathlib import Path
from typing import cast
from urllib.parse import urlencode
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import pandas as pd

BASE_OUTPUT_DIR = Path("data/raw/ft")
DEFAULT_TIMEOUT_SECONDS = 20.0
DEFAULT_RETRIES = 3
DEFAULT_THROTTLE_SECONDS = 0.5
USER_AGENT = "Mozilla/5.0 (compatible; ft-sector-collector/1.0)"
FT_AJAX_UPDATE_RESULTS_URL = "https://markets.ft.com/data/equities/ajax/updateScreenerResults"
US_IWV_HOLDINGS_CSV_BASE_URL = (
    "https://www.ishares.com/us/products/239714/"
    "ishares-russell-3000-etf/1467271812596.ajax"
)
US_IWV_HOLDINGS_QUERY = "fileType=csv&fileName=IWV_holdings&dataType=fund"
US_IWV_MAX_LOOKBACK_DAYS = 10
JPX_TOPIX_WEIGHT_CSV_URL = "https://www.jpx.co.jp/automation/english/markets/indices/topix/files/topixweight_e.csv"

TOPIX500_SERIES_CODES = {
    "TOPIX Core30",
    "TOPIX Large70",
    "TOPIX Mid400",
}

US_DEFAULT_FT_EXCHANGE_CODES = {
    "NSQ",
    "NMQ",
    "NAQ",
    "NYQ",
    "NYS",
    "ASE",
    "ASQ",
}

US_EXCHANGE_NAME_TO_FT_CODES = {
    "NASDAQ": {"NSQ", "NMQ", "NAQ"},
    "NEW YORK STOCK EXCHANGE": {"NYQ", "NYS"},
    "NYSE AMERICAN": {"ASE", "ASQ"},
}

US_MEMBERSHIP_SECTOR_TO_ICB_L1 = {
    "Communication": "Telecommunications",
    "Consumer Discretionary": "Consumer Discretionary",
    "Consumer Staples": "Consumer Staples",
    "Energy": "Energy",
    "Financials": "Financials",
    "Health Care": "Health Care",
    "Industrials": "Industrials",
    "Information Technology": "Technology",
    "Materials": "Basic Materials",
    "Real Estate": "Real Estate",
    "Utilities": "Utilities",
}


FT_SORT_PAYLOAD = {
    "field": "RCCFTStandardName",
    "direction": "ascending",
}


TARGET_ROWS_BY_UNIVERSE = {
    "russell3000": 3000,
    "topix500": 500,
}


MARKET_TO_UNIVERSE = {
    "us": "russell3000",
    "jp": "topix500",
}


@dataclass(frozen=True)
class MarketConfig:
    results_urls: tuple[str, ...]
    tearsheet_url: str
    country_code: str
    country_label: str
    currency_code: str
    default_target_rows: int
    default_universe_scope: str


@dataclass(frozen=True)
class CollectorOptions:
    market: str
    as_of: str
    parser: str
    universe_scope: str
    target_rows: int
    timeout: float
    retries: int
    throttle: float
    force: bool


@dataclass(frozen=True)
class MembershipFilter:
    source_name: str
    stock_codes: set[str]
    preferred_exchange_codes: dict[str, set[str]]
    level1_hints: dict[str, str]


JsonDict = dict[str, object]


MARKET_CONFIGS: dict[str, MarketConfig] = {
    "us": MarketConfig(
        results_urls=(
            "https://markets.ft.com/data/equities/results?market=US",
            "https://markets.ft.com/data/equities/results",
        ),
        tearsheet_url="https://markets.ft.com/data/equities/tearsheet/summary?s=AAPL:NSQ",
        country_code="US",
        country_label="United States",
        currency_code="USD",
        default_target_rows=3000,
        default_universe_scope="russell3000",
    ),
    "jp": MarketConfig(
        results_urls=(
            "https://markets.ft.com/data/equities/results?market=JP",
            "https://markets.ft.com/data/equities/results",
        ),
        tearsheet_url="https://markets.ft.com/data/equities/tearsheet/summary?s=7203:TYO",
        country_code="JP",
        country_label="Japan",
        currency_code="JPY",
        default_target_rows=500,
        default_universe_scope="topix500",
    ),
}


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _validate_as_of(value: str) -> str:
    try:
        _ = date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("--as-of must be in YYYY-MM-DD format") from exc
    return value


def _request_text(url: str, timeout_seconds: float) -> str:
    request = Request(url=url, headers={"User-Agent": USER_AGENT})
    with urlopen(request, timeout=timeout_seconds) as response_any:  # noqa: S310  # pyright: ignore[reportAny]
        response = cast(HTTPResponse, response_any)
        body = response.read()
    return body.decode("utf-8", errors="replace")


def _fetch_with_retry(
    *,
    url: str,
    retries: int,
    timeout_seconds: float,
    throttle_seconds: float,
) -> str:
    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            return _request_text(url=url, timeout_seconds=timeout_seconds)
        except (HTTPError, URLError, TimeoutError, OSError, IncompleteRead) as exc:
            last_error = exc
            if attempt == retries:
                break
            time.sleep(throttle_seconds)
    if last_error is None:
        raise RuntimeError("request failed without exception")
    raise RuntimeError(f"request failed after {retries} attempts: {last_error}") from last_error


def _fetch_first_available(
    *,
    urls: tuple[str, ...],
    retries: int,
    timeout_seconds: float,
    throttle_seconds: float,
) -> tuple[str, str]:
    errors: list[str] = []
    for candidate in urls:
        try:
            html = _fetch_with_retry(
                url=candidate,
                retries=retries,
                timeout_seconds=timeout_seconds,
                throttle_seconds=throttle_seconds,
            )
            return candidate, html
        except RuntimeError as exc:
            errors.append(f"{candidate}: {exc}")
    joined = " | ".join(errors)
    raise RuntimeError(f"all candidate URLs failed: {joined}")


def _request_json(url: str, payload: dict[str, str], timeout_seconds: float, referer: str) -> JsonDict:
    encoded = urlencode(payload).encode("utf-8")
    request = Request(
        url=url,
        data=encoded,
        headers={
            "User-Agent": USER_AGENT,
            "Referer": referer,
            "X-Requested-With": "XMLHttpRequest",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        },
    )
    with urlopen(request, timeout=timeout_seconds) as response_any:  # noqa: S310  # pyright: ignore[reportAny]
        response = cast(HTTPResponse, response_any)
        body = response.read()
    payload_obj = cast(object, json.loads(body.decode("utf-8", errors="replace")))
    if not isinstance(payload_obj, dict):
        raise RuntimeError("ft ajax response was not an object")
    return cast(JsonDict, payload_obj)


def _fetch_json_with_retry(
    *,
    url: str,
    payload: dict[str, str],
    retries: int,
    timeout_seconds: float,
    throttle_seconds: float,
    referer: str,
) -> JsonDict:
    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            return _request_json(url=url, payload=payload, timeout_seconds=timeout_seconds, referer=referer)
        except (HTTPError, URLError, TimeoutError, OSError, IncompleteRead, json.JSONDecodeError) as exc:
            last_error = exc
            if attempt == retries:
                break
            time.sleep(throttle_seconds)
    if last_error is None:
        raise RuntimeError("request failed without exception")
    raise RuntimeError(f"request failed after {retries} attempts: {last_error}") from last_error


def _parse_with_html_parser(html_text: str) -> JsonDict:
    title_match = re.search(r"<title>(.*?)</title>", html_text, flags=re.IGNORECASE | re.DOTALL)
    title = title_match.group(1).strip() if title_match else ""
    return {
        "parser_mode": "html",
        "title": title,
    }


def _parse_with_js_engine(html_text: str) -> JsonDict:
    script_match = re.search(
        r"window\.__INITIAL_STATE__\s*=\s*(\{.*?\})\s*;",
        html_text,
        flags=re.DOTALL,
    )
    extracted: dict[str, object] = {}
    if script_match:
        payload = script_match.group(1)
        try:
            state_obj = cast(object, json.loads(payload))
        except json.JSONDecodeError:
            state_obj = cast(object, {"_raw_length": len(payload)})
        if isinstance(state_obj, dict):
            extracted = cast(dict[str, object], state_obj)
    return {
        "parser_mode": "js-engine",
        "has_embedded_state": bool(script_match),
        "embedded_state_keys": sorted(extracted.keys())[:20],
    }


def _parse_page(html_text: str, parser_name: str) -> JsonDict:
    if parser_name == "js-engine":
        return _parse_with_js_engine(html_text)
    return _parse_with_html_parser(html_text)


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _ = path.write_text(content, encoding="utf-8")


def _write_json(path: Path, payload: JsonDict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _ = path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _write_parquet(path: Path, frame: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        frame.to_parquet(path, index=False)
    except ImportError as exc:
        raise RuntimeError("parquet engine missing. Install pyarrow or fastparquet") from exc


def _normalize_whitespace(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _clean_html_text(value: str) -> str:
    text = html.unescape(value)
    text = re.sub(r"<[^>]+>", " ", text)
    return _normalize_whitespace(text)


def _target_rows_for_scope(scope: str, default_target: int) -> int:
    token = scope.strip().lower()
    return TARGET_ROWS_BY_UNIVERSE.get(token, default_target)


def _normalize_stock_code(value: str) -> str:
    return value.strip().upper()


def _canonical_stock_code(value: str) -> str:
    normalized = _normalize_stock_code(value)
    return re.sub(r"[^A-Z0-9]", "", normalized)


def _split_ft_ticker(ticker: str) -> tuple[str, str] | None:
    parts = [part.strip().upper() for part in ticker.split(":") if part.strip()]
    if len(parts) < 2:
        return None
    stock_code = parts[0]
    exchange_code = parts[1]
    if not stock_code or not exchange_code:
        return None
    return stock_code, exchange_code


def _map_us_exchange_name_to_ft_codes(exchange_name: str) -> set[str]:
    upper = exchange_name.strip().upper()
    matched: set[str] = set()
    for key, value in US_EXCHANGE_NAME_TO_FT_CODES.items():
        if key in upper:
            matched.update(value)
    if not matched:
        matched.update(US_DEFAULT_FT_EXCHANGE_CODES)
    return matched


def _map_us_membership_sector_to_icb_l1(sector_name: str) -> str:
    return US_MEMBERSHIP_SECTOR_TO_ICB_L1.get(sector_name.strip(), "")


def _build_us_iwv_holdings_url(as_of_yyyymmdd: str) -> str:
    return f"{US_IWV_HOLDINGS_CSV_BASE_URL}?{US_IWV_HOLDINGS_QUERY}&asOfDate={as_of_yyyymmdd}"


def _parse_us_iwv_holdings_csv(raw_csv: str) -> pd.DataFrame:
    marker = "\nTicker,Name,Sector,Asset Class"
    idx = raw_csv.find(marker)
    if idx == -1:
        raise RuntimeError("unexpected iShares holdings csv format")

    frame = cast(pd.DataFrame, pd.read_csv(io.StringIO(raw_csv[idx + 1 :])))
    required_columns = {"Ticker", "Asset Class", "Exchange", "Location"}
    missing = sorted(required_columns.difference(frame.columns))
    if missing:
        raise RuntimeError("iShares holdings csv missing columns: " + ",".join(missing))
    return frame


def _fetch_us_iwv_equity_rows(options: CollectorOptions) -> tuple[pd.DataFrame, str]:
    as_of_date = date.fromisoformat(options.as_of)
    last_error: RuntimeError | None = None
    for offset in range(US_IWV_MAX_LOOKBACK_DAYS + 1):
        candidate = as_of_date - timedelta(days=offset)
        candidate_date = candidate.strftime("%Y%m%d")
        candidate_url = _build_us_iwv_holdings_url(candidate_date)
        try:
            raw_csv = _fetch_with_retry(
                url=candidate_url,
                retries=options.retries,
                timeout_seconds=options.timeout,
                throttle_seconds=options.throttle,
            )
            frame = _parse_us_iwv_holdings_csv(raw_csv)
        except RuntimeError as exc:
            last_error = exc
            continue

        asset_class = cast(pd.Series, frame["Asset Class"]).astype(str).str.strip().str.lower()
        equity_rows = cast(pd.DataFrame, frame[asset_class == "equity"].copy())
        location_series = cast(pd.Series, equity_rows["Location"]).astype(str).str.strip().str.lower()
        equity_rows = cast(pd.DataFrame, equity_rows[location_series == "united states"].copy())
        if not equity_rows.empty:
            return equity_rows, candidate_url

    if last_error is not None:
        raise RuntimeError(
            f"failed to fetch IWV holdings within lookback={US_IWV_MAX_LOOKBACK_DAYS}: {last_error}"
        ) from last_error
    raise RuntimeError(
        f"IWV holdings did not contain US equity rows within lookback={US_IWV_MAX_LOOKBACK_DAYS}"
    )


def _load_us_russell3000_membership(options: CollectorOptions) -> MembershipFilter:
    equity_rows, source_url = _fetch_us_iwv_equity_rows(options)

    stock_codes: set[str] = set()
    preferred_exchange_codes: dict[str, set[str]] = {}
    level1_hints: dict[str, str] = {}
    equity_rows_records = cast(list[dict[str, object]], cast(pd.DataFrame, equity_rows).to_dict(orient="records"))
    for row in equity_rows_records:
        stock_code = _normalize_stock_code(str(row.get("Ticker", "")))
        stock_code_key = _canonical_stock_code(stock_code)
        if not stock_code_key or stock_code == "-":
            continue
        stock_codes.add(stock_code_key)
        exchange_codes = _map_us_exchange_name_to_ft_codes(str(row.get("Exchange", "")))
        existing = preferred_exchange_codes.get(stock_code_key)
        if existing is None:
            preferred_exchange_codes[stock_code_key] = set(exchange_codes)
        else:
            existing.update(exchange_codes)

        sector_hint = _map_us_membership_sector_to_icb_l1(str(row.get("Sector", "")))
        if sector_hint:
            level1_hints[stock_code_key] = sector_hint

    if not stock_codes:
        raise RuntimeError("iShares holdings csv produced empty Russell membership set")

    return MembershipFilter(
        source_name=source_url,
        stock_codes=stock_codes,
        preferred_exchange_codes=preferred_exchange_codes,
        level1_hints=level1_hints,
    )


def _collect_us_russell3000_from_iwv(options: CollectorOptions) -> tuple[pd.DataFrame, dict[str, object]]:
    equity_rows, source_url = _fetch_us_iwv_equity_rows(options)
    records = cast(list[dict[str, object]], equity_rows.to_dict(orient="records"))

    seen_stock_codes: set[str] = set()
    collected: list[dict[str, object]] = []
    for row in records:
        stock_code = _normalize_stock_code(str(row.get("Ticker", "")))
        stock_code_key = _canonical_stock_code(stock_code)
        if not stock_code_key or stock_code == "-":
            continue
        if stock_code_key in seen_stock_codes:
            continue

        exchange_codes = sorted(_map_us_exchange_name_to_ft_codes(str(row.get("Exchange", ""))))
        exchange_code = exchange_codes[0] if exchange_codes else "NSQ"
        sector_hint = _map_us_membership_sector_to_icb_l1(str(row.get("Sector", "")))
        sector_value = str(row.get("Sector", "")).strip()
        if sector_value:
            native_level3 = sector_value
        elif sector_hint:
            native_level3 = sector_hint
        else:
            native_level3 = "ICB_L3_UNCERTAIN"

        seen_stock_codes.add(stock_code_key)
        collected.append(
            {
                "as_of_date": options.as_of,
                "market": "us",
                "universe_scope": options.universe_scope,
                "ticker": f"{stock_code}:{exchange_code}",
                "stock_code": stock_code,
                "exchange_code": exchange_code,
                "stock_name": str(row.get("Name", "")).strip() or stock_code,
                "country_label": "United States",
                "native_taxonomy_level1": sector_hint,
                "native_taxonomy_label": native_level3,
                "tearsheet_url": source_url,
            }
        )

    frame = pd.DataFrame(collected)
    if not frame.empty:
        frame = frame.sort_values(["ticker"], kind="stable").reset_index(drop=True)

    stats: dict[str, object] = {
        "pages_fetched": 0,
        "max_page_seen": 0,
        "target_rows": int(len(frame)),
        "constituent_rows": int(len(frame)),
        "constituent_unique_stock_codes": int(len(frame)),
        "membership_size": int(len(frame)),
        "membership_source": source_url,
        "membership_coverage_ratio": 1.0,
    }
    return frame, stats


def _load_jp_topix500_membership(options: CollectorOptions) -> MembershipFilter:
    raw_csv = _fetch_with_retry(
        url=JPX_TOPIX_WEIGHT_CSV_URL,
        retries=options.retries,
        timeout_seconds=options.timeout,
        throttle_seconds=options.throttle,
    )
    frame = cast(pd.DataFrame, pd.read_csv(io.StringIO(raw_csv), dtype={"Code": "string"}))
    required_columns = {"Code", "New Index Series Code"}
    missing = sorted(required_columns.difference(frame.columns))
    if missing:
        raise RuntimeError("JPX TOPIX csv missing columns: " + ",".join(missing))

    series_codes = cast(pd.Series, frame["New Index Series Code"])
    topix500_rows = frame[series_codes.isin(sorted(TOPIX500_SERIES_CODES))].copy()
    if topix500_rows.empty:
        raise RuntimeError("JPX TOPIX csv produced empty TOPIX500 membership set")

    stock_codes = {
        _canonical_stock_code(str(value))
        for value in topix500_rows["Code"].astype(str).tolist()
        if _canonical_stock_code(str(value))
    }
    preferred_exchange_codes = {code: {"TYO"} for code in stock_codes}

    return MembershipFilter(
        source_name=JPX_TOPIX_WEIGHT_CSV_URL,
        stock_codes=stock_codes,
        preferred_exchange_codes=preferred_exchange_codes,
        level1_hints={},
    )


def _resolve_membership_filter(*, market: str, scope: str, options: CollectorOptions) -> MembershipFilter | None:
    scope_token = scope.strip().lower()
    if market == "us" and scope_token == "russell3000":
        return _load_us_russell3000_membership(options)
    if market == "jp" and scope_token == "topix500":
        return _load_jp_topix500_membership(options)
    return None


def _extract_ft_params(results_html: str, country_code: str) -> str:
    match = re.search(r'name="params"\s+value="(.*?)"', results_html, flags=re.DOTALL | re.IGNORECASE)
    if not match:
        raise RuntimeError("could not locate FT screener params in results html")

    raw = html.unescape(match.group(1))
    payload_obj = cast(object, json.loads(raw))
    if not isinstance(payload_obj, list) or not payload_obj:
        raise RuntimeError("invalid FT screener params payload")
    first = payload_obj[0]
    if not isinstance(first, dict):
        raise RuntimeError("invalid FT screener params first clause")

    first["Clauses"] = [{"Operator": 4, "Values": [country_code]}]
    normalized = json.dumps(payload_obj, separators=(",", ":"), ensure_ascii=True)
    return normalized


def _extract_last_page(fragment_html: str, current_page: int) -> int:
    page_values = cast(list[str], re.findall(r'data-mod-pagination-num="(\d+)"', fragment_html))
    values = [int(value) for value in page_values]
    if not values:
        return current_page
    return max(values)


def _parse_ajax_rows(fragment_html: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    row_matches = cast(list[str], re.findall(r"<tr>(.*?)</tr>", fragment_html, flags=re.DOTALL | re.IGNORECASE))
    for row_html in row_matches:
        if "tearsheet/summary?s=" not in row_html:
            continue

        symbol_match = re.search(r"/data/equities/tearsheet/summary\?s=([^\"&]+)", row_html)
        if not symbol_match:
            continue
        ticker = symbol_match.group(1).strip()
        split = _split_ft_ticker(ticker)
        if split is None:
            continue
        stock_code, exchange_code = split

        cell_matches = cast(
            list[str],
            re.findall(r'<td class="mod-ui-table__cell--text">(.*?)</td>', row_html, flags=re.DOTALL),
        )
        if len(cell_matches) < 3:
            continue

        first_cell = cell_matches[0]
        name_match = re.search(r'<span class="mod-ui-hide-xsmall">(.*?)</span>', first_cell, flags=re.DOTALL)
        stock_name_raw = name_match.group(1) if name_match else first_cell

        stock_name = _clean_html_text(stock_name_raw)
        country_label = _clean_html_text(cell_matches[1])
        industry_label = _clean_html_text(cell_matches[2])
        rows.append(
            {
                "ticker": ticker,
                "stock_code": stock_code,
                "exchange_code": exchange_code,
                "stock_name": stock_name,
                "country_label": country_label,
                "native_taxonomy_label": industry_label,
                "tearsheet_url": f"https://markets.ft.com/data/equities/tearsheet/summary?s={ticker}",
            }
        )
    return rows


FT_LEVEL1_NAMES = (
    "Basic Materials",
    "Consumer Discretionary",
    "Consumer Staples",
    "Energy",
    "Financials",
    "Health Care",
    "Industrials",
    "Real Estate",
    "Technology",
    "Telecommunications",
    "Utilities",
)


def _extract_tearsheet_taxonomy(tearsheet_html: str) -> tuple[str, str] | None:
    explicit = re.search(
        r'mod-tearsheet-overview__esi">\s*([^<]+?)\s*<i[^>]*></i>\s*([^<]+?)\s*</div>',
        tearsheet_html,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if explicit:
        level1 = _clean_html_text(explicit.group(1))
        level3 = _clean_html_text(explicit.group(2))
        if level1 and level3:
            return level1, level3

    fallback = re.search(
        r'mod-tearsheet-overview__esi">(.*?)</div>',
        tearsheet_html,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if not fallback:
        return None

    combined = _clean_html_text(fallback.group(1))
    for level1_name in FT_LEVEL1_NAMES:
        if combined.startswith(level1_name):
            level3 = combined[len(level1_name) :].strip()
            if level3:
                return level1_name, level3
    return None


def _load_tearsheet_cache(cache_path: Path) -> dict[str, tuple[str, str]]:
    if not cache_path.exists():
        return {}

    frame = cast(pd.DataFrame, pd.read_parquet(cache_path))
    required = {"ticker", "icb_level1", "icb_level3"}
    missing = sorted(required.difference(frame.columns))
    if missing:
        return {}

    cache: dict[str, tuple[str, str]] = {}
    for row in cast(list[dict[str, object]], frame.to_dict(orient="records")):
        ticker = str(row.get("ticker", "")).strip()
        if not ticker:
            continue
        level1 = str(row.get("icb_level1", "")).strip()
        level3 = str(row.get("icb_level3", "")).strip()
        if level3:
            cache[ticker] = (level1, level3)
    return cache


def _write_tearsheet_cache(cache_path: Path, cache: dict[str, tuple[str, str]]) -> None:
    if not cache:
        return

    rows = [
        {
            "ticker": ticker,
            "icb_level1": values[0],
            "icb_level3": values[1],
            "updated_at": _utc_now_iso(),
        }
        for ticker, values in sorted(cache.items())
    ]
    frame = pd.DataFrame(rows)
    _write_parquet(cache_path, frame)


def _enrich_missing_taxonomy_from_tearsheet(
    *,
    frame: pd.DataFrame,
    options: CollectorOptions,
    cache_path: Path,
) -> tuple[pd.DataFrame, dict[str, object]]:
    if frame.empty:
        return frame, {
            "unknown_before": 0,
            "unknown_after": 0,
            "cache_hits": 0,
            "fetched_success": 0,
            "fetched_failed": 0,
        }

    enriched = frame.copy()
    if "native_taxonomy_level1" not in enriched.columns:
        enriched["native_taxonomy_level1"] = ""

    taxonomy_series = cast(pd.Series, enriched["native_taxonomy_label"]).astype(str).str.strip()
    unknown_mask = taxonomy_series.isin(["", "--", "ICB_L3_UNCERTAIN"])
    unknown_indices = cast(list[int], enriched.index[unknown_mask].tolist())
    unknown_before = len(unknown_indices)
    if unknown_before == 0:
        return enriched, {
            "unknown_before": 0,
            "unknown_after": 0,
            "cache_hits": 0,
            "fetched_success": 0,
            "fetched_failed": 0,
        }

    cache = _load_tearsheet_cache(cache_path)
    cache_changed = False
    cache_hits = 0
    fetched_success = 0
    fetched_failed = 0

    for index_value in unknown_indices:
        ticker = str(enriched.at[index_value, "ticker"]).strip()
        cached = cache.get(ticker)
        if cached is not None:
            enriched.at[index_value, "native_taxonomy_level1"] = cached[0]
            enriched.at[index_value, "native_taxonomy_label"] = cached[1]
            cache_hits += 1
            continue

        tearsheet_url = str(enriched.at[index_value, "tearsheet_url"]).strip()
        if not tearsheet_url:
            fetched_failed += 1
            continue

        try:
            tearsheet_html = _fetch_with_retry(
                url=tearsheet_url,
                retries=max(1, min(options.retries, 2)),
                timeout_seconds=options.timeout,
                throttle_seconds=options.throttle,
            )
        except RuntimeError:
            fetched_failed += 1
            continue

        taxonomy = _extract_tearsheet_taxonomy(tearsheet_html)
        if taxonomy is None:
            fetched_failed += 1
            continue

        level1, level3 = taxonomy
        enriched.at[index_value, "native_taxonomy_level1"] = level1
        enriched.at[index_value, "native_taxonomy_label"] = level3
        cache[ticker] = (level1, level3)
        cache_changed = True
        fetched_success += 1

    if cache_changed:
        _write_tearsheet_cache(cache_path, cache)

    unknown_after = int(
        cast(pd.Series, enriched["native_taxonomy_label"])
        .astype(str)
        .str.strip()
        .isin(["", "--", "ICB_L3_UNCERTAIN"])
        .sum()
    )

    return enriched, {
        "unknown_before": unknown_before,
        "unknown_after": unknown_after,
        "cache_hits": cache_hits,
        "fetched_success": fetched_success,
        "fetched_failed": fetched_failed,
        "cache_path": cache_path.as_posix(),
    }


def _apply_membership_level1_hints(frame: pd.DataFrame, membership_filter: MembershipFilter | None) -> pd.DataFrame:
    if frame.empty or membership_filter is None or not membership_filter.level1_hints:
        return frame

    updated = frame.copy()
    if "native_taxonomy_level1" not in updated.columns:
        updated["native_taxonomy_level1"] = ""

    stock_code_keys = cast(pd.Series, updated["stock_code"]).astype(str).map(_canonical_stock_code)
    level1_series = cast(pd.Series, updated["native_taxonomy_level1"]).astype(str).str.strip()
    missing_mask = level1_series.eq("")
    missing_indexes = cast(list[int], updated.index[missing_mask].tolist())
    for index_value in missing_indexes:
        key = str(stock_code_keys.at[index_value])
        hint = membership_filter.level1_hints.get(key, "")
        if hint:
            updated.at[index_value, "native_taxonomy_level1"] = hint
    return updated


def _fetch_results_fragment(
    *,
    data_param: str,
    page: int,
    currency_code: str,
    retries: int,
    timeout_seconds: float,
    throttle_seconds: float,
    referer: str,
) -> str:
    payload = {
        "data": data_param,
        "page": str(page),
        "currencyCode": currency_code,
        "sort": json.dumps(FT_SORT_PAYLOAD, separators=(",", ":")),
    }
    response = _fetch_json_with_retry(
        url=FT_AJAX_UPDATE_RESULTS_URL,
        payload=payload,
        retries=retries,
        timeout_seconds=timeout_seconds,
        throttle_seconds=throttle_seconds,
        referer=referer,
    )
    html_fragment = str(response.get("html", ""))
    if not html_fragment:
        raise RuntimeError("ft ajax response missing html fragment")
    return html_fragment


def _collect_market_constituents(
    *,
    market: str,
    results_html: str,
    config: MarketConfig,
    options: CollectorOptions,
    referer: str,
    membership_filter: MembershipFilter | None,
) -> tuple[pd.DataFrame, dict[str, object]]:
    data_param = _extract_ft_params(results_html=results_html, country_code=config.country_code)

    page = 1
    max_page = 1
    seen_tickers: set[str] = set()
    seen_stock_codes: set[str] = set()
    collected: list[dict[str, object]] = []
    pages_fetched = 0
    target_rows = max(options.target_rows, 1)
    country_expected = config.country_label.strip().lower()
    membership_size = 0
    if membership_filter is not None:
        membership_size = len(membership_filter.stock_codes)
        if membership_size > 0:
            target_rows = min(target_rows, membership_size)

    while page <= max_page and len(seen_stock_codes) < target_rows:
        fragment = _fetch_results_fragment(
            data_param=data_param,
            page=page,
            currency_code=config.currency_code,
            retries=options.retries,
            timeout_seconds=options.timeout,
            throttle_seconds=options.throttle,
            referer=referer,
        )
        pages_fetched += 1
        max_page = max(max_page, _extract_last_page(fragment_html=fragment, current_page=page))

        parsed_rows = _parse_ajax_rows(fragment)
        if not parsed_rows and page > 1:
            break

        for row in parsed_rows:
            ticker = str(row["ticker"])
            stock_code = _normalize_stock_code(str(row["stock_code"]))
            stock_code_key = _canonical_stock_code(stock_code)
            exchange_code = str(row.get("exchange_code", "")).strip().upper()
            if ticker in seen_tickers:
                continue
            if stock_code_key in seen_stock_codes:
                continue

            if str(row["country_label"]).strip().lower() != country_expected:
                continue

            if membership_filter is not None:
                if stock_code_key not in membership_filter.stock_codes:
                    continue
                preferred_codes = membership_filter.preferred_exchange_codes.get(stock_code_key)
                if preferred_codes and exchange_code not in preferred_codes:
                    continue

            level1_hint = ""
            if membership_filter is not None:
                level1_hint = membership_filter.level1_hints.get(stock_code_key, "")

            seen_tickers.add(ticker)
            seen_stock_codes.add(stock_code_key)
            collected.append(
                {
                    "as_of_date": options.as_of,
                    "market": market,
                    "universe_scope": options.universe_scope,
                    "ticker": ticker,
                    "stock_code": stock_code,
                    "exchange_code": exchange_code,
                    "stock_name": str(row["stock_name"]),
                    "country_label": str(row["country_label"]),
                    "native_taxonomy_level1": level1_hint,
                    "native_taxonomy_label": str(row["native_taxonomy_label"]),
                    "tearsheet_url": str(row["tearsheet_url"]),
                }
            )
            if len(seen_stock_codes) >= target_rows:
                break

        page += 1
        if len(seen_stock_codes) < target_rows and page <= max_page:
            time.sleep(options.throttle)

    frame = pd.DataFrame(collected)
    if not frame.empty:
        frame = frame.sort_values(["ticker"], kind="stable").reset_index(drop=True)

    return frame, {
        "pages_fetched": pages_fetched,
        "max_page_seen": max_page,
        "target_rows": target_rows,
        "constituent_rows": int(len(frame)),
        "constituent_unique_stock_codes": len(seen_stock_codes),
        "membership_size": membership_size,
        "membership_source": membership_filter.source_name if membership_filter is not None else "none",
        "membership_coverage_ratio": (len(seen_stock_codes) / membership_size) if membership_size else 1.0,
    }


def _run_collection(options: CollectorOptions) -> tuple[int, JsonDict]:
    market = options.market
    config = MARKET_CONFIGS[market]
    use_us_iwv_source = market == "us" and options.universe_scope == "russell3000"
    output_dir = BASE_OUTPUT_DIR / options.as_of / market
    metadata_path = output_dir / "run_metadata.json"
    results_path = output_dir / "results_page_1.html"
    constituents_path = output_dir / "constituents.parquet"
    tearsheet_path = output_dir / "tearsheet_sample.html"
    tearsheet_cache_path = output_dir / "tearsheet_taxonomy_cache.parquet"

    metadata: JsonDict = {
        "run_id": str(uuid.uuid4()),
        "as_of": options.as_of,
        "market": market,
        "universe_scope": options.universe_scope,
        "target_rows": options.target_rows,
        "parser": options.parser,
        "js_engine": "enabled" if options.parser == "js-engine" else "disabled",
        "headless_browser_used": False,
        "headless_browser_used_text": "false",
        "output_dir": output_dir.as_posix(),
        "constituents_path": constituents_path.as_posix(),
        "started_at": _utc_now_iso(),
        "status": "started",
        "results_url": None,
        "tearsheet_url": config.tearsheet_url,
        "checkpoint_hit": False,
        "error": None,
    }

    try:
        if use_us_iwv_source:
            parsed_results = {"parser_mode": "iwv-csv"}
            constituents, collect_stats = _collect_us_russell3000_from_iwv(options)
            _write_parquet(constituents_path, constituents)
            metadata["results_url"] = "source://ishares-iwv-csv"
            metadata["membership_source"] = str(collect_stats["membership_source"])
            membership_size_obj = collect_stats.get("membership_size", 0)
            if isinstance(membership_size_obj, (int, float)):
                membership_size = int(float(membership_size_obj))
            elif isinstance(membership_size_obj, str):
                try:
                    membership_size = int(membership_size_obj)
                except ValueError:
                    membership_size = 0
            else:
                membership_size = 0
            metadata["membership_size"] = membership_size
            membership_filter = None
        else:
            if results_path.exists() and not options.force:
                results_html = results_path.read_text(encoding="utf-8")
                metadata["checkpoint_hit"] = True
                metadata["results_url"] = "checkpoint://results_page_1.html"
            else:
                selected_url, results_html = _fetch_first_available(
                    urls=config.results_urls,
                    retries=options.retries,
                    timeout_seconds=options.timeout,
                    throttle_seconds=options.throttle,
                )
                _write_text(results_path, results_html)
                metadata["results_url"] = selected_url
                _ = time.sleep(options.throttle)

            parsed_results = _parse_page(results_html, parser_name=options.parser)
            membership_filter = _resolve_membership_filter(market=market, scope=options.universe_scope, options=options)
            metadata["membership_source"] = membership_filter.source_name if membership_filter is not None else "none"
            metadata["membership_size"] = len(membership_filter.stock_codes) if membership_filter is not None else 0

            if constituents_path.exists() and not options.force:
                existing = pd.read_parquet(constituents_path)
                constituents = cast(pd.DataFrame, existing)
                cached_unique_codes = (
                    cast(pd.Series, constituents["stock_code"]).astype(str).map(_canonical_stock_code).nunique()
                    if not constituents.empty
                    else 0
                )
                required_codes = options.target_rows
                if membership_filter is not None and membership_filter.stock_codes:
                    required_codes = max(1, int(len(membership_filter.stock_codes) * 0.98))
                metadata["constituents_checkpoint_hit"] = bool(cached_unique_codes >= required_codes)
                if cached_unique_codes < required_codes:
                    constituents, collect_stats = _collect_market_constituents(
                        market=market,
                        results_html=results_html,
                        config=config,
                        options=options,
                        referer=str(metadata["results_url"]),
                        membership_filter=membership_filter,
                    )
                    _write_parquet(constituents_path, constituents)
                else:
                    collect_stats = {
                        "pages_fetched": 0,
                        "max_page_seen": 0,
                        "target_rows": options.target_rows,
                        "constituent_rows": int(len(constituents)),
                        "constituent_unique_stock_codes": int(cached_unique_codes),
                        "membership_size": len(membership_filter.stock_codes) if membership_filter is not None else 0,
                        "membership_source": membership_filter.source_name if membership_filter is not None else "none",
                        "membership_coverage_ratio": (
                            (cached_unique_codes / len(membership_filter.stock_codes))
                            if membership_filter is not None and membership_filter.stock_codes
                            else 1.0
                        ),
                    }
            else:
                constituents, collect_stats = _collect_market_constituents(
                    market=market,
                    results_html=results_html,
                    config=config,
                    options=options,
                    referer=str(metadata["results_url"]),
                    membership_filter=membership_filter,
                )
                _write_parquet(constituents_path, constituents)

        if len(constituents) == 0:
            raise RuntimeError(f"no FT constituents collected for market={market}")

        constituents = _apply_membership_level1_hints(
            frame=constituents,
            membership_filter=membership_filter,
        )

        constituents, enrichment_stats = _enrich_missing_taxonomy_from_tearsheet(
            frame=constituents,
            options=options,
            cache_path=tearsheet_cache_path,
        )
        _write_parquet(constituents_path, constituents)
        collect_summary = cast(JsonDict, dict(collect_stats))
        collect_summary["tearsheet_enrichment"] = enrichment_stats
        metadata["tearsheet_enrichment"] = enrichment_stats

        ratio_obj = collect_summary.get("membership_coverage_ratio", 1.0)
        if isinstance(ratio_obj, (int, float)):
            coverage_ratio = float(ratio_obj)
        elif isinstance(ratio_obj, str):
            try:
                coverage_ratio = float(ratio_obj)
            except ValueError:
                coverage_ratio = 0.0
        else:
            coverage_ratio = 0.0
        if membership_filter is not None and coverage_ratio < 0.95:
            raise RuntimeError(
                f"membership coverage too low for market={market}: {coverage_ratio:.4f} (< 0.95)"
            )

        sample_url = str(constituents.iloc[0]["tearsheet_url"])
        metadata["tearsheet_url"] = sample_url

        if use_us_iwv_source:
            parsed_tearsheet = {"parser_mode": "iwv-csv", "skipped": True}
        else:
            if tearsheet_path.exists() and not options.force:
                tearsheet_html = tearsheet_path.read_text(encoding="utf-8")
            else:
                tearsheet_html = _fetch_with_retry(
                    url=sample_url,
                    retries=options.retries,
                    timeout_seconds=options.timeout,
                    throttle_seconds=options.throttle,
                )
                _write_text(tearsheet_path, tearsheet_html)

            parsed_tearsheet = _parse_page(tearsheet_html, parser_name=options.parser)
        metadata["parse_summary"] = {
            "results": parsed_results,
            "tearsheet": parsed_tearsheet,
        }
        metadata["collection_summary"] = collect_summary
        metadata["status"] = "success"
        return 0, metadata
    except Exception as exc:  # noqa: BLE001
        metadata["status"] = "failed"
        metadata["error"] = str(exc)
        return 1, metadata
    finally:
        metadata["finished_at"] = _utc_now_iso()
        _write_json(metadata_path, metadata)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Collect FT US/JP pages")
    _ = parser.add_argument("--market", choices=tuple(MARKET_CONFIGS.keys()), required=True)
    _ = parser.add_argument("--as-of", required=True)
    _ = parser.add_argument("--parser", choices=("html", "js-engine"), default="html")
    _ = parser.add_argument("--universe-scope", default="")
    _ = parser.add_argument("--target-rows", type=int, default=0)
    _ = parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_SECONDS)
    _ = parser.add_argument("--retries", type=int, default=DEFAULT_RETRIES)
    _ = parser.add_argument("--throttle", type=float, default=DEFAULT_THROTTLE_SECONDS)
    _ = parser.add_argument("--force", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        as_of = _validate_as_of(cast(str, args.as_of))
    except ValueError as exc:
        parser.error(str(exc))

    market = cast(str, args.market)
    config = MARKET_CONFIGS[market]
    scope_value = cast(str, args.universe_scope).strip().lower() or config.default_universe_scope
    target_rows_arg = cast(int, args.target_rows)
    target_rows = target_rows_arg if target_rows_arg > 0 else _target_rows_for_scope(
        scope=scope_value,
        default_target=config.default_target_rows,
    )

    options = CollectorOptions(
        market=market,
        as_of=as_of,
        parser=cast(str, args.parser),
        universe_scope=scope_value,
        target_rows=target_rows,
        timeout=cast(float, args.timeout),
        retries=cast(int, args.retries),
        throttle=cast(float, args.throttle),
        force=cast(bool, args.force),
    )
    exit_code, metadata = _run_collection(options)
    print(f"status={metadata['status']}")
    print(f"market={metadata['market']}")
    print(f"output_dir={metadata['output_dir']}")
    print(f"js_engine={metadata['js_engine']}")
    print("headless_browser_used=false")
    if metadata.get("error"):
        print(f"error={metadata['error']}")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
