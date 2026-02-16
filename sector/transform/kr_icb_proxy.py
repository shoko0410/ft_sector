"""Build deterministic KR to ICB proxy crosswalk with confidence scoring."""
# pyright: reportMissingTypeStubs=false,reportUnknownMemberType=false,reportUnknownVariableType=false,reportUnknownArgumentType=false,reportAttributeAccessIssue=false,reportUnnecessaryCast=false

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pandas as pd

DEFAULT_AS_OF = "2026-01-31"
DEFAULT_INPUT_TEMPLATE = "data/raw/kr/{as_of}/constituents.parquet"
DEFAULT_OUTPUT_PATH = Path("data/normalized/kr_icb_proxy_crosswalk.parquet")
DEFAULT_METADATA_PATH = Path("data/normalized/kr_icb_proxy_crosswalk_run_metadata.json")
MAPPING_VERSION = "kr_icb_proxy_v1"
MAPPING_METHOD = "deterministic_fixed_crosswalk_and_rules"
ICB_L1_UNKNOWN = "ICB_UNKNOWN"
ICB_L3_UNCERTAIN = "ICB_L3_UNCERTAIN"


@dataclass(frozen=True)
class MappingResult:
    icb_proxy_level1: str
    icb_proxy_level3: str
    confidence_score: float
    mapping_method: str


TICKER_RULES: dict[str, tuple[str, str, float]] = {
    "005930": ("Technology", "Technology Hardware and Equipment", 0.93),
    "000660": ("Technology", "Semiconductors and Semiconductor Equipment", 0.95),
    "035420": ("Technology", "Software and Computer Services", 0.92),
    "051910": ("Basic Materials", "Chemicals", 0.91),
    "207940": ("Health Care", "Pharmaceuticals and Biotechnology", 0.94),
    "247540": ("Industrials", "Electronic and Electrical Equipment", 0.88),
    "066970": ("Industrials", "Electronic and Electrical Equipment", 0.87),
    "263750": ("Consumer Discretionary", "Media", 0.88),
    "293490": ("Consumer Discretionary", "Media", 0.87),
    "091990": ("Health Care", "Pharmaceuticals and Biotechnology", 0.86),
}

NATIVE_LABEL_CROSSWALK: dict[str, tuple[str, str, float]] = {
    "반도체와반도체장비": ("Technology", "Semiconductors and Semiconductor Equipment", 0.90),
    "반도체": ("Technology", "Semiconductors and Semiconductor Equipment", 0.89),
    "IT서비스": ("Technology", "Software and Computer Services", 0.86),
    "소프트웨어": ("Technology", "Software and Computer Services", 0.85),
    "화학": ("Basic Materials", "Chemicals", 0.86),
    "철강": ("Basic Materials", "Basic Resources", 0.84),
    "비철금속": ("Basic Materials", "Basic Resources", 0.84),
    "포장재": ("Basic Materials", "Chemicals", 0.82),
    "생물공학": ("Health Care", "Pharmaceuticals and Biotechnology", 0.86),
    "제약": ("Health Care", "Pharmaceuticals and Biotechnology", 0.88),
    "건강관리장비와서비스": ("Health Care", "Health Care Equipment and Services", 0.84),
    "건강관리장비와용품": ("Health Care", "Health Care Equipment and Services", 0.85),
    "건강관리업체및서비스": ("Health Care", "Health Care Equipment and Services", 0.83),
    "생명과학도구및서비스": ("Health Care", "Health Care Equipment and Services", 0.82),
    "건강관리기술": ("Health Care", "Health Care Equipment and Services", 0.82),
    "게임엔터테인먼트": ("Consumer Discretionary", "Media", 0.84),
    "방송과엔터테인먼트": ("Consumer Discretionary", "Media", 0.84),
    "양방향미디어와서비스": ("Consumer Discretionary", "Media", 0.83),
    "광고": ("Consumer Discretionary", "Media", 0.82),
    "자동차": ("Consumer Discretionary", "Automobiles and Parts", 0.84),
    "자동차부품": ("Consumer Discretionary", "Automobiles and Parts", 0.84),
    "백화점과일반상점": ("Consumer Discretionary", "Retailers", 0.84),
    "인터넷과카탈로그소매": ("Consumer Discretionary", "Retailers", 0.82),
    "호텔레스토랑레저": ("Consumer Discretionary", "Travel and Leisure", 0.84),
    "섬유의류신발호화품": ("Consumer Discretionary", "Personal Goods", 0.83),
    "가정용기기와용품": ("Consumer Discretionary", "Household Goods and Home Construction", 0.82),
    "가구": ("Consumer Discretionary", "Household Goods and Home Construction", 0.82),
    "화장품": ("Consumer Discretionary", "Personal Goods", 0.83),
    "교육서비스": ("Consumer Discretionary", "Consumer Services", 0.82),
    "식품": ("Consumer Staples", "Food, Beverage and Tobacco", 0.85),
    "음료": ("Consumer Staples", "Food, Beverage and Tobacco", 0.84),
    "담배": ("Consumer Staples", "Food, Beverage and Tobacco", 0.84),
    "은행": ("Financials", "Banks", 0.88),
    "증권": ("Financials", "Financial Services", 0.86),
    "카드": ("Financials", "Financial Services", 0.84),
    "창업투자": ("Financials", "Financial Services", 0.83),
    "보험": ("Financials", "Insurance", 0.87),
    "손해보험": ("Financials", "Insurance", 0.86),
    "생명보험": ("Financials", "Insurance", 0.86),
    "무선통신서비스": ("Telecommunications", "Telecommunications Service Providers", 0.86),
    "다각화된통신서비스": ("Telecommunications", "Telecommunications Service Providers", 0.86),
    "유틸리티": ("Utilities", "Utilities", 0.82),
    "전기유틸리티": ("Utilities", "Utilities", 0.84),
    "가스유틸리티": ("Utilities", "Utilities", 0.84),
    "복합유틸리티": ("Utilities", "Utilities", 0.84),
    "석유와가스": ("Energy", "Oil, Gas and Coal", 0.84),
    "에너지장비및서비스": ("Energy", "Oil, Gas and Coal", 0.82),
    "전자장비와기기": ("Technology", "Technology Hardware and Equipment", 0.84),
    "통신장비": ("Technology", "Technology Hardware and Equipment", 0.83),
    "핸드셋": ("Technology", "Technology Hardware and Equipment", 0.83),
    "디스플레이장비및부품": ("Technology", "Technology Hardware and Equipment", 0.83),
    "디스플레이패널": ("Technology", "Technology Hardware and Equipment", 0.83),
    "전자제품": ("Technology", "Technology Hardware and Equipment", 0.82),
    "기계": ("Industrials", "Industrial Engineering", 0.84),
    "전기장비": ("Industrials", "Industrial Engineering", 0.84),
    "전기제품": ("Industrials", "Industrial Engineering", 0.83),
    "조선": ("Industrials", "Industrial Engineering", 0.84),
    "우주항공과국방": ("Industrials", "Industrial Engineering", 0.84),
    "복합기업": ("Industrials", "Industrial Support Services", 0.82),
    "건설": ("Industrials", "Construction and Materials", 0.84),
    "건축자재": ("Industrials", "Construction and Materials", 0.84),
    "상업서비스와공급품": ("Industrials", "Industrial Support Services", 0.83),
    "무역회사와판매업체": ("Industrials", "Industrial Support Services", 0.82),
    "항공사": ("Industrials", "Transportation", 0.84),
    "항공화물운송과물류": ("Industrials", "Transportation", 0.84),
    "해운사": ("Industrials", "Transportation", 0.84),
}

L1_KEYWORD_RULES: tuple[tuple[tuple[str, ...], str, float], ...] = (
    (("반도체", "IT", "소프트", "인터넷", "플랫폼", "게임"), "Technology", 0.70),
    (("은행", "증권", "보험", "금융"), "Financials", 0.72),
    (("바이오", "제약", "의료", "헬스"), "Health Care", 0.70),
    (("화학", "소재", "철강"), "Basic Materials", 0.70),
    (("자동차", "소매", "소비", "호텔"), "Consumer Discretionary", 0.68),
)


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _normalize_native_label(label: str) -> str:
    value = label.strip()
    for token in (" ", ",", "&", "·"):
        value = value.replace(token, "")
    return value


def _confidence_band(score: float) -> str:
    if score >= 0.80:
        return "high"
    if score >= 0.60:
        return "medium"
    return "low"


def _map_row(stock_code: str, native_taxonomy_label: str) -> MappingResult:
    normalized_label = _normalize_native_label(native_taxonomy_label)

    ticker_result = TICKER_RULES.get(stock_code)
    if ticker_result is not None:
        return MappingResult(
            icb_proxy_level1=ticker_result[0],
            icb_proxy_level3=ticker_result[1],
            confidence_score=ticker_result[2],
            mapping_method="ticker_rule",
        )

    exact = NATIVE_LABEL_CROSSWALK.get(normalized_label)
    if exact is not None:
        return MappingResult(
            icb_proxy_level1=exact[0],
            icb_proxy_level3=exact[1],
            confidence_score=exact[2],
            mapping_method="native_label_exact",
        )

    for keywords, level1, score in L1_KEYWORD_RULES:
        if any(keyword in normalized_label for keyword in keywords):
            return MappingResult(
                icb_proxy_level1=level1,
                icb_proxy_level3=ICB_L3_UNCERTAIN,
                confidence_score=score,
                mapping_method="native_label_keyword_level1",
            )

    return MappingResult(
        icb_proxy_level1=ICB_L1_UNKNOWN,
        icb_proxy_level3=ICB_L3_UNCERTAIN,
        confidence_score=0.20,
        mapping_method="fallback_unknown",
    )


def _read_input(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise RuntimeError(f"input parquet does not exist: {path.as_posix()}")
    frame = pd.read_parquet(path)
    required = {
        "as_of_date",
        "stock_code",
        "stock_name",
        "native_taxonomy_label",
        "taxonomy_source",
        "taxonomy_version",
    }
    missing = sorted(required.difference(frame.columns))
    if missing:
        raise RuntimeError("input parquet missing required columns: " + ",".join(missing))
    return cast(pd.DataFrame, frame)


def build_crosswalk(input_path: Path, as_of: str) -> pd.DataFrame:
    raw = _read_input(input_path)
    base = (
        raw.sort_values(["stock_code", "index_name"], kind="stable")
        .drop_duplicates(subset=["stock_code"], keep="first")
        .reset_index(drop=True)
    )

    mapping_rows: list[dict[str, object]] = []
    for row in base.itertuples(index=False):
        stock_code = str(row.stock_code).zfill(6)
        native_label = str(row.native_taxonomy_label)
        result = _map_row(stock_code=stock_code, native_taxonomy_label=native_label)
        mapping_rows.append(
            {
                "as_of_date": str(row.as_of_date),
                "stock_code": stock_code,
                "stock_name": str(row.stock_name),
                "native_taxonomy_label": native_label,
                "taxonomy_source": str(row.taxonomy_source),
                "taxonomy_version": str(row.taxonomy_version),
                "icb_proxy_level1": result.icb_proxy_level1,
                "icb_proxy_level3": result.icb_proxy_level3,
                "mapping_version": MAPPING_VERSION,
                "mapping_method": result.mapping_method,
                "confidence_score": round(float(result.confidence_score), 4),
                "confidence_band": _confidence_band(float(result.confidence_score)),
                "mapping_effective_date": as_of,
            }
        )

    mapped = pd.DataFrame(mapping_rows)
    mapped["icb_proxy_level3"] = mapped["icb_proxy_level3"].fillna(ICB_L3_UNCERTAIN)
    return mapped.sort_values(["stock_code"], kind="stable").reset_index(drop=True)


def _write_parquet(path: Path, frame: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        frame.to_parquet(path, index=False)
    except ImportError as exc:
        raise RuntimeError("parquet engine missing. Install pyarrow or fastparquet") from exc


def _write_metadata(path: Path, *, as_of: str, input_path: Path, output_path: Path, rows: int) -> None:
    metadata = {
        "status": "success",
        "as_of": as_of,
        "input_path": input_path.as_posix(),
        "output_path": output_path.as_posix(),
        "mapping_version": MAPPING_VERSION,
        "mapping_method": MAPPING_METHOD,
        "llm_calls": 0,
        "deterministic_rules_only": True,
        "row_count": rows,
        "generated_at": _utc_now_iso(),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    _ = path.write_text(json.dumps(metadata, ensure_ascii=True, indent=2, sort_keys=True), encoding="utf-8")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build KR to ICB proxy crosswalk")
    _ = parser.add_argument("--as-of", default=DEFAULT_AS_OF)
    _ = parser.add_argument("--input", default="")
    _ = parser.add_argument("--output", default=DEFAULT_OUTPUT_PATH.as_posix())
    _ = parser.add_argument("--metadata", default=DEFAULT_METADATA_PATH.as_posix())
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    as_of = cast(str, args.as_of)
    input_value = cast(str, args.input)
    input_path = Path(input_value) if input_value else Path(DEFAULT_INPUT_TEMPLATE.format(as_of=as_of))
    output_path = Path(cast(str, args.output))
    metadata_path = Path(cast(str, args.metadata))
    try:
        mapped = build_crosswalk(input_path=input_path, as_of=as_of)
        _write_parquet(output_path, mapped)
        _write_metadata(
            metadata_path,
            as_of=as_of,
            input_path=input_path,
            output_path=output_path,
            rows=len(mapped),
        )
    except RuntimeError as exc:
        print("result=FAIL")
        print(f"error={exc}")
        return 1

    print("result=PASS")
    print(f"as_of={as_of}")
    print(f"output={output_path.as_posix()}")
    print(f"rows={len(mapped)}")
    print(f"mapping_version={MAPPING_VERSION}")
    print(f"mapping_method={MAPPING_METHOD}")
    print("llm_calls=0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
