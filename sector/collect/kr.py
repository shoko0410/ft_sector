"""Collect KR constituents and native taxonomy with deterministic fallbacks."""
# pyright: reportMissingTypeStubs=false,reportUnknownMemberType=false,reportUnknownVariableType=false,reportUnknownArgumentType=false,reportUnknownLambdaType=false

from __future__ import annotations

import argparse
import html as html_lib
import http.cookiejar
import json
import re
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from http.client import HTTPResponse
from pathlib import Path
from typing import cast
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPCookieProcessor, OpenerDirector, Request, build_opener, urlopen

import pandas as pd

BASE_OUTPUT_DIR = Path("data/raw/kr")
DEFAULT_TIMEOUT_SECONDS = 15.0
DEFAULT_RETRIES = 3
DEFAULT_THROTTLE_SECONDS = 0.3
DEFAULT_MAX_NULL_RATE = 0.05
USER_AGENT = "Mozilla/5.0 (compatible; ft-sector-kr-collector/1.0)"
SUPPORTED_INDICES = ("kospi200", "kosdaq150")
KRX_GET_JSON_URL = "http://data.krx.co.kr/comm/bldAttendant/getJsonData.cmd"
KRX_REFERER_URL = "https://data.krx.co.kr/contents/MDC/MDI/outerLoader/index.cmd"
KRX_INDEX_FINDER_BLD = "dbms/comm/finder/finder_equidx"
KRX_CONSTITUENTS_BLD = "dbms/MDC/STAT/standard/MDCSTAT00601"
KRX_MAX_LOOKBACK_DAYS = 7
WISEINDEX_TREE_URL = "https://www.wiseindex.com/API/Tree/Get?id=4"
WISEINDEX_COMPONENTS_URL = "https://www.wiseindex.com/Index/GetIndexComponets"
WISEINDEX_TREE_ROOT_KEY = "0000004"
WISEINDEX_WICS_NODE_KEY = "0000010021"
WISEINDEX_MAX_LOOKBACK_DAYS = 10
WISEINDEX_SECTOR_LABELS = {
    "G10": "에너지",
    "G15": "소재",
    "G20": "산업재",
    "G25": "경기관련소비재",
    "G30": "필수소비재",
    "G35": "건강관리",
    "G40": "금융",
    "G45": "IT",
    "G50": "커뮤니케이션서비스",
    "G55": "유틸리티",
}
KRX_INDEX_NAME_HINTS = {
    "kospi200": ("코스피200", "코스피 200", "KOSPI200", "KOSPI 200"),
    "kosdaq150": ("코스닥150", "코스닥 150", "KOSDAQ150", "KOSDAQ 150"),
}


@dataclass(frozen=True)
class CollectorOptions:
    indices: tuple[str, ...]
    as_of: str
    timeout: float
    retries: int
    throttle: float
    force_naver_fail: bool
    max_null_rate: float
    allow_local_fallback: bool


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _validate_as_of(value: str) -> str:
    try:
        _ = date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("--as-of must be in YYYY-MM-DD format") from exc
    return value


def _parse_indices(value: str) -> tuple[str, ...]:
    candidates = [item.strip().lower() for item in value.split(",") if item.strip()]
    if not candidates:
        raise ValueError("--indices must contain at least one index")

    deduped: list[str] = []
    seen: set[str] = set()
    for item in candidates:
        if item not in SUPPORTED_INDICES:
            supported = ",".join(SUPPORTED_INDICES)
            raise ValueError(f"unsupported index '{item}'. supported={supported}")
        if item not in seen:
            seen.add(item)
            deduped.append(item)
    return tuple(deduped)


def _request_text(url: str, timeout_seconds: float) -> str:
    request = Request(url=url, headers={"User-Agent": USER_AGENT})
    with urlopen(request, timeout=timeout_seconds) as response_any:  # noqa: S310  # pyright: ignore[reportAny]
        response = cast(HTTPResponse, response_any)
        content_type = response.headers.get("Content-Type")
        body = response.read()
    return _decode_response_body(body=body, content_type=content_type)


def _extract_charset(content_type: str | None) -> str | None:
    if not content_type:
        return None
    match = re.search(r"charset\s*=\s*['\"]?([^;\s'\"]+)", content_type, flags=re.IGNORECASE)
    if not match:
        return None
    return match.group(1).strip()


def _decode_response_body(*, body: bytes, content_type: str | None) -> str:
    preferred = _extract_charset(content_type)
    candidates = [preferred, "utf-8", "cp949", "euc-kr"]
    attempted: set[str] = set()
    for charset in candidates:
        if not charset or charset in attempted:
            continue
        attempted.add(charset)
        try:
            return body.decode(charset)
        except (LookupError, UnicodeDecodeError):
            continue
    return body.decode("utf-8", errors="replace")


def _build_krx_opener() -> OpenerDirector:
    cookie_jar = http.cookiejar.CookieJar()
    opener = build_opener(HTTPCookieProcessor(cookie_jar))
    opener.addheaders = [
        ("User-Agent", "Chrome/78.0.3904.87 Safari/537.36"),
        ("Referer", KRX_REFERER_URL),
    ]
    return opener


def _request_json(url: str, payload: Mapping[str, object], timeout_seconds: float) -> dict[str, object]:
    opener = _build_krx_opener()
    warmup_request = Request(url=KRX_REFERER_URL, headers={"User-Agent": USER_AGENT})
    try:
        with opener.open(warmup_request, timeout=timeout_seconds) as warmup_response_any:  # pyright: ignore[reportAny]
            warmup_response = cast(HTTPResponse, warmup_response_any)
            _ = warmup_response.read(1)
    except (HTTPError, URLError, TimeoutError, OSError):
        pass

    encoded = urlencode({key: str(value) for key, value in payload.items()}).encode("utf-8")
    request = Request(
        url=url,
        data=encoded,
        headers={
            "User-Agent": "Chrome/78.0.3904.87 Safari/537.36",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "Referer": KRX_REFERER_URL,
        },
    )
    with opener.open(request, timeout=timeout_seconds) as response_any:  # noqa: S310  # pyright: ignore[reportAny]
        response = cast(HTTPResponse, response_any)
        content_type = response.headers.get("Content-Type")
        body = response.read()
    payload_obj = cast(object, json.loads(_decode_response_body(body=body, content_type=content_type)))
    if not isinstance(payload_obj, dict):
        raise RuntimeError("KRX response was not a JSON object")
    return cast(dict[str, object], payload_obj)


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
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            last_error = exc
            if attempt == retries:
                break
            time.sleep(throttle_seconds)
    if last_error is None:
        raise RuntimeError("request failed without exception")
    raise RuntimeError(f"request failed after {retries} attempts: {last_error}") from last_error


def _fetch_json_with_retry(
    *,
    payload: Mapping[str, object],
    retries: int,
    timeout_seconds: float,
    throttle_seconds: float,
) -> dict[str, object]:
    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            return _request_json(
                url=KRX_GET_JSON_URL,
                payload=payload,
                timeout_seconds=timeout_seconds,
            )
        except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            last_error = exc
            if attempt == retries:
                break
            time.sleep(throttle_seconds)
    if last_error is None:
        raise RuntimeError("request failed without exception")
    raise RuntimeError(f"request failed after {retries} attempts: {last_error}") from last_error


def _load_wiseindex_sector_codes(options: CollectorOptions) -> tuple[str, ...]:
    tree_text = _fetch_with_retry(
        url=WISEINDEX_TREE_URL,
        retries=options.retries,
        timeout_seconds=options.timeout,
        throttle_seconds=options.throttle,
    )
    payload_obj = cast(object, json.loads(tree_text))
    if not isinstance(payload_obj, list):
        raise RuntimeError("WiseIndex tree response was not a list")

    root_node: dict[str, object] | None = None
    for row_obj in payload_obj:
        if not isinstance(row_obj, dict):
            continue
        row = cast(dict[str, object], row_obj)
        if str(row.get("key", "")) == WISEINDEX_TREE_ROOT_KEY:
            root_node = row
            break
    if root_node is None:
        raise RuntimeError("WiseIndex tree missing Wise Sector root")

    root_children = root_node.get("children")
    if not isinstance(root_children, list):
        raise RuntimeError("WiseIndex tree root missing children")

    wics_node: dict[str, object] | None = None
    for row_obj in root_children:
        if not isinstance(row_obj, dict):
            continue
        row = cast(dict[str, object], row_obj)
        if str(row.get("key", "")) == WISEINDEX_WICS_NODE_KEY:
            wics_node = row
            break
    if wics_node is None:
        raise RuntimeError("WiseIndex tree missing WICS node")

    wics_children = wics_node.get("children")
    if not isinstance(wics_children, list):
        raise RuntimeError("WiseIndex WICS node missing children")

    sector_codes: set[str] = set()
    for level3_obj in wics_children:
        if not isinstance(level3_obj, dict):
            continue
        level3 = cast(dict[str, object], level3_obj)
        level4_children = level3.get("children")
        if not isinstance(level4_children, list):
            continue
        for level4_obj in level4_children:
            if not isinstance(level4_obj, dict):
                continue
            key = str(cast(dict[str, object], level4_obj).get("key", "")).strip().upper()
            if key.startswith("G") and len(key) == 5:
                sector_codes.add(key)

    if not sector_codes:
        raise RuntimeError("WiseIndex tree did not provide WICS sector codes")
    return tuple(sorted(sector_codes))


def _fetch_wiseindex_components(
    *,
    sec_cd: str,
    dt_yyyymmdd: str,
    options: CollectorOptions,
) -> tuple[list[dict[str, object]], str]:
    params = urlencode({"ceil_yn": "0", "dt": dt_yyyymmdd, "sec_cd": sec_cd})
    url = f"{WISEINDEX_COMPONENTS_URL}?{params}"
    payload_text = _fetch_with_retry(
        url=url,
        retries=options.retries,
        timeout_seconds=options.timeout,
        throttle_seconds=options.throttle,
    )
    payload_obj = cast(object, json.loads(payload_text))
    if not isinstance(payload_obj, dict):
        raise RuntimeError(f"WiseIndex components response was not an object for sec_cd={sec_cd}")
    list_obj = payload_obj.get("list")
    if not isinstance(list_obj, list):
        return [], url
    rows: list[dict[str, object]] = []
    for row_obj in list_obj:
        if isinstance(row_obj, dict):
            rows.append(cast(dict[str, object], row_obj))
    return rows, url


def _extract_wiseindex_sector_label(row: Mapping[str, object], sec_cd: str) -> str:
    sec_name = str(row.get("SEC_NM_KOR", "")).strip()
    if sec_name:
        return sec_name

    idx_name = str(row.get("IDX_NM_KOR", "")).strip()
    if idx_name:
        cleaned = idx_name.replace("WICS", "").strip()
        if cleaned:
            return cleaned

    return WISEINDEX_SECTOR_LABELS.get(sec_cd[:3], f"WICS_{sec_cd}")


def _fetch_wiseindex_wics_map(
    *,
    as_of: str,
    options: CollectorOptions,
) -> tuple[dict[str, tuple[str, str]], str]:
    sector_codes = _load_wiseindex_sector_codes(options)
    as_of_date = date.fromisoformat(as_of)
    last_error: RuntimeError | None = None
    for offset in range(WISEINDEX_MAX_LOOKBACK_DAYS + 1):
        candidate_date = (as_of_date - timedelta(days=offset)).strftime("%Y%m%d")
        by_stock: dict[str, tuple[str, str]] = {}
        for sec_cd in sector_codes:
            try:
                rows, url = _fetch_wiseindex_components(sec_cd=sec_cd, dt_yyyymmdd=candidate_date, options=options)
            except RuntimeError as exc:
                last_error = exc
                continue
            for row in rows:
                stock_code = str(row.get("CMP_CD", "")).strip().zfill(6)
                if not stock_code or stock_code == "000000":
                    continue
                sector_label = _extract_wiseindex_sector_label(row, sec_cd)
                by_stock[stock_code] = (sector_label, url)
        if by_stock:
            return by_stock, candidate_date

    if last_error is not None:
        raise RuntimeError(
            f"WiseIndex WICS retrieval failed within lookback={WISEINDEX_MAX_LOOKBACK_DAYS}: {last_error}"
        ) from last_error
    raise RuntimeError(f"WiseIndex WICS returned empty map within lookback={WISEINDEX_MAX_LOOKBACK_DAYS}")


def _fixture_constituents() -> dict[str, list[dict[str, str]]]:
    return {
        "kospi200": [
            {"stock_code": "005930", "stock_name": "Samsung Electronics"},
            {"stock_code": "000660", "stock_name": "SK hynix"},
            {"stock_code": "035420", "stock_name": "NAVER"},
            {"stock_code": "051910", "stock_name": "LG Chem"},
            {"stock_code": "207940", "stock_name": "Samsung Biologics"},
        ],
        "kosdaq150": [
            {"stock_code": "247540", "stock_name": "Ecopro BM"},
            {"stock_code": "066970", "stock_name": "L&F"},
            {"stock_code": "263750", "stock_name": "Pearl Abyss"},
            {"stock_code": "293490", "stock_name": "Kakao Games"},
            {"stock_code": "091990", "stock_name": "Celltrion HC"},
        ],
    }


def _normalize_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9가-힣]", "", value).upper()


def _resolve_krx_index_codes(options: CollectorOptions) -> dict[str, tuple[str, str]]:
    payload = {
        "bld": KRX_INDEX_FINDER_BLD,
        "mktsel": "1",
    }
    response = _fetch_json_with_retry(
        payload=payload,
        retries=options.retries,
        timeout_seconds=options.timeout,
        throttle_seconds=options.throttle,
    )
    block_obj = response.get("block1")
    if not isinstance(block_obj, list):
        raise RuntimeError("KRX index finder response missing block1")

    remaining = set(options.indices)
    resolved: dict[str, tuple[str, str]] = {}
    for row_obj in block_obj:
        if not isinstance(row_obj, dict):
            continue
        row = cast(dict[str, object], row_obj)
        code_name = str(row.get("codeName", ""))
        normalized_name = _normalize_name(code_name)
        full_code = str(row.get("full_code", "")).strip()
        short_code = str(row.get("short_code", "")).strip()
        if not full_code or not short_code:
            continue
        for index_name in tuple(remaining):
            hints = KRX_INDEX_NAME_HINTS[index_name]
            if any(_normalize_name(hint) == normalized_name for hint in hints):
                resolved[index_name] = (full_code, short_code)
                remaining.remove(index_name)
                break

    if remaining:
        missing = ",".join(sorted(remaining))
        raise RuntimeError(f"KRX index code resolution failed for: {missing}")
    return resolved


def _fetch_krx_constituents(index_name: str, as_of: str, options: CollectorOptions) -> list[dict[str, str]]:
    code_map = _resolve_krx_index_codes(options)
    group_id, ticker = code_map[index_name]
    as_of_date = date.fromisoformat(as_of)
    last_error: RuntimeError | None = None
    for offset in range(KRX_MAX_LOOKBACK_DAYS + 1):
        candidate_trade_date = (as_of_date - timedelta(days=offset)).strftime("%Y%m%d")
        payload = {
            "bld": KRX_CONSTITUENTS_BLD,
            "trdDd": candidate_trade_date,
            "indIdx": group_id,
            "indIdx2": ticker,
            "param1indIdx_finder_equidx0_1": "",
            "money": "1",
            "csvxls_isNo": "false",
        }
        try:
            response = _fetch_json_with_retry(
                payload=payload,
                retries=options.retries,
                timeout_seconds=options.timeout,
                throttle_seconds=options.throttle,
            )
        except RuntimeError as exc:
            last_error = exc
            continue

        output_obj = response.get("output")
        if not isinstance(output_obj, list):
            continue

        rows: list[dict[str, str]] = []
        for row_obj in output_obj:
            if not isinstance(row_obj, dict):
                continue
            row = cast(dict[str, object], row_obj)
            stock_code = str(row.get("ISU_SRT_CD", "")).strip()
            stock_name = str(row.get("ISU_ABBRV", "")).strip()
            if not stock_code or not stock_name:
                continue
            rows.append({"stock_code": stock_code.zfill(6), "stock_name": stock_name})

        if rows:
            return rows

    if last_error is not None:
        raise RuntimeError(
            f"KRX constituents retrieval failed for {index_name} after lookback={KRX_MAX_LOOKBACK_DAYS}: {last_error}"
        ) from last_error
    raise RuntimeError(
        f"KRX constituents response empty for {index_name} after lookback={KRX_MAX_LOOKBACK_DAYS}"
    )


def _build_constituents(indices: tuple[str, ...], as_of: str, options: CollectorOptions) -> pd.DataFrame:
    fixture = _fixture_constituents()
    rows: list[dict[str, object]] = []
    for index_name in indices:
        constituent_source = "krx_primary"
        source_error: str | None = None
        index_items: list[dict[str, str]]
        try:
            index_items = _fetch_krx_constituents(index_name=index_name, as_of=as_of, options=options)
        except RuntimeError as exc:
            if not options.allow_local_fallback:
                raise
            index_items = fixture[index_name]
            constituent_source = "local_fallback"
            source_error = str(exc)

        for item in index_items:
            rows.append(
                {
                    "as_of_date": as_of,
                    "index_name": index_name,
                    "stock_code": item["stock_code"],
                    "stock_name": item["stock_name"],
                    "exchange": "KRX",
                    "country": "kr",
                    "constituent_source": constituent_source,
                    "constituent_source_error": source_error,
                }
            )

    frame = pd.DataFrame(rows)
    return frame.sort_values(["stock_code", "index_name"], kind="stable").reset_index(drop=True)


def _extract_naver_wics_label(html_text: str) -> str | None:
    patterns = (
        r"WICS\s*:\s*</em>\s*<a[^>]*>([^<]+)</a>",
        r"WICS\s*:\s*</span>\s*<a[^>]*>([^<]+)</a>",
        r"WICS\s*:\s*([^<\n]+)",
        r"업종명\s*:\s*<a[^>]*>([^<]+)</a>",
        r"업종\s*:\s*<a[^>]*>([^<]+)</a>",
    )
    for pattern in patterns:
        match = re.search(pattern, html_text, flags=re.IGNORECASE)
        if match:
            label = html_lib.unescape(match.group(1))
            label = re.sub(r"\s+", " ", label).strip()
            if label:
                return label
    return None


def _fetch_naver_wics(
    *,
    stock_code: str,
    retries: int,
    timeout_seconds: float,
    throttle_seconds: float,
) -> tuple[str | None, str | None]:
    url = f"https://finance.naver.com/item/main.naver?code={stock_code}"
    html_text = _fetch_with_retry(
        url=url,
        retries=retries,
        timeout_seconds=timeout_seconds,
        throttle_seconds=throttle_seconds,
    )
    label = _extract_naver_wics_label(html_text)
    return url, label


def _fallback_label(index_names: list[str]) -> str:
    labels = [f"KRX_{name.upper()}_MEMBER" for name in sorted(index_names)]
    return ",".join(labels)


def _collect_taxonomy(
    *,
    constituents: pd.DataFrame,
    options: CollectorOptions,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    index_map = (
        constituents.groupby("stock_code")["index_name"]
        .apply(lambda values: sorted({str(item) for item in values.to_list()}))
        .to_dict()
    )
    name_map = constituents.groupby("stock_code")["stock_name"].first().to_dict()

    wiseindex_map: dict[str, tuple[str, str]] = {}
    wiseindex_dt: str | None = None
    wiseindex_error: str | None = None
    try:
        wiseindex_map, wiseindex_dt = _fetch_wiseindex_wics_map(as_of=options.as_of, options=options)
    except RuntimeError as exc:
        wiseindex_error = str(exc)

    taxonomy_rows: list[dict[str, object]] = []
    naver_rows: list[dict[str, object]] = []
    for stock_code in sorted(index_map.keys()):
        stock_name = str(name_map[stock_code])
        source_url: str | None = None
        source_label: str | None = None
        naver_label: str | None = None
        naver_error: str | None = None
        wise_entry = wiseindex_map.get(str(stock_code).zfill(6))
        if wise_entry is not None:
            source_label = wise_entry[0]
            source_url = wise_entry[1]
        elif not options.force_naver_fail:
            try:
                source_url, naver_label = _fetch_naver_wics(
                    stock_code=stock_code,
                    retries=options.retries,
                    timeout_seconds=options.timeout,
                    throttle_seconds=options.throttle,
                )
                source_label = naver_label
            except RuntimeError as exc:
                naver_error = str(exc)
            time.sleep(options.throttle)
        else:
            naver_error = "forced by --force-naver-fail"

        if wise_entry is not None:
            taxonomy_source = "wiseindex_wics_primary"
            native_taxonomy_label = str(source_label)
        elif source_label:
            taxonomy_source = "naver_wics_primary"
            native_taxonomy_label = source_label
        else:
            taxonomy_source = "krx_fallback"
            native_taxonomy_label = _fallback_label(index_map[stock_code])

        taxonomy_rows.append(
            {
                "as_of_date": options.as_of,
                "stock_code": stock_code,
                "stock_name": stock_name,
                "native_taxonomy_label": native_taxonomy_label,
                "taxonomy_source": taxonomy_source,
                "taxonomy_version": options.as_of,
            }
        )
        naver_rows.append(
            {
                "as_of_date": options.as_of,
                "stock_code": stock_code,
                "stock_name": stock_name,
                "wics_label": source_label,
                "taxonomy_source": taxonomy_source,
                "source_url": source_url,
                "error": naver_error or wiseindex_error,
                "wiseindex_dt": wiseindex_dt,
                "source_ts": _utc_now_iso(),
            }
        )

    taxonomy_frame = pd.DataFrame(taxonomy_rows)
    naver_frame = pd.DataFrame(naver_rows)
    return taxonomy_frame, naver_frame


def _assert_null_rate(frame: pd.DataFrame, max_null_rate: float) -> float:
    null_rate = float(frame["native_taxonomy_label"].isna().mean())
    if null_rate > max_null_rate:
        raise RuntimeError(
            f"native_taxonomy_label null-rate exceeded threshold: null_rate={null_rate:.4f}, max_null_rate={max_null_rate:.4f}"
        )
    return null_rate


def _write_parquet(path: Path, frame: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        frame.to_parquet(path, index=False)
    except ImportError as exc:
        raise RuntimeError(
            "parquet engine missing. Install pyarrow or fastparquet to run sector.collect.kr"
        ) from exc


def _run_collection(options: CollectorOptions) -> int:
    output_dir = BASE_OUTPUT_DIR / options.as_of
    constituents_path = output_dir / "constituents.parquet"
    naver_wics_path = output_dir / "naver_wics.parquet"

    constituents = _build_constituents(options.indices, options.as_of, options)
    taxonomy_frame, naver_wics_frame = _collect_taxonomy(constituents=constituents, options=options)

    merged = constituents.merge(
        taxonomy_frame,
        on=["as_of_date", "stock_code", "stock_name"],
        how="left",
        validate="many_to_one",
    )
    null_rate = _assert_null_rate(merged, options.max_null_rate)

    _write_parquet(constituents_path, merged)
    _write_parquet(naver_wics_path, naver_wics_frame)

    source_values = sorted(set(cast(list[str], merged["taxonomy_source"].dropna().unique().tolist())))
    source_summary = source_values[0] if len(source_values) == 1 else "mixed(" + ",".join(source_values) + ")"

    print("status=success")
    print(f"indices={','.join(options.indices)}")
    print(f"as_of={options.as_of}")
    print(f"output_dir={output_dir.as_posix()}")
    constituent_sources = sorted(
        set(cast(list[str], merged["constituent_source"].dropna().unique().tolist()))
    )
    constituent_source_summary = (
        constituent_sources[0]
        if len(constituent_sources) == 1
        else "mixed(" + ",".join(constituent_sources) + ")"
    )
    print(f"constituent_source={constituent_source_summary}")
    print(f"taxonomy_source={source_summary}")
    print(f"null_rate.native_taxonomy_label={null_rate:.4f}")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Collect KR constituents + Naver WICS taxonomy")
    _ = parser.add_argument("--indices", required=True, help="comma-separated list: kospi200,kosdaq150")
    _ = parser.add_argument("--as-of", required=True)
    _ = parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_SECONDS)
    _ = parser.add_argument("--retries", type=int, default=DEFAULT_RETRIES)
    _ = parser.add_argument("--throttle", type=float, default=DEFAULT_THROTTLE_SECONDS)
    _ = parser.add_argument("--max-null-rate", type=float, default=DEFAULT_MAX_NULL_RATE)
    _ = parser.add_argument("--force-naver-fail", action="store_true")
    _ = parser.add_argument("--no-local-fallback", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        as_of = _validate_as_of(cast(str, args.as_of))
        indices = _parse_indices(cast(str, args.indices))
    except ValueError as exc:
        parser.error(str(exc))

    options = CollectorOptions(
        indices=indices,
        as_of=as_of,
        timeout=cast(float, args.timeout),
        retries=cast(int, args.retries),
        throttle=cast(float, args.throttle),
        force_naver_fail=cast(bool, args.force_naver_fail),
        max_null_rate=cast(float, args.max_null_rate),
        allow_local_fallback=not cast(bool, args.no_local_fallback),
    )
    try:
        return _run_collection(options)
    except RuntimeError as exc:
        print("status=failed")
        print(f"error={exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
