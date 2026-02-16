# pyright: reportMissingImports=false,reportUnknownVariableType=false,reportPrivateUsage=false
import pytest

from sector.collect import kr
from sector.collect.kr import CollectorOptions
from sector.collect.kr import _extract_naver_wics_label
from sector.collect.kr import _extract_wiseindex_sector_label


def test_extract_naver_wics_label_wics_pattern() -> None:
    html = "<em>WICS :</em><a href='#'>반도체와반도체장비</a>"
    assert _extract_naver_wics_label(html) == "반도체와반도체장비"


def test_extract_naver_wics_label_industry_name_pattern() -> None:
    html = "동종업종비교(업종명 : <a href='/sise/sise_group_detail.naver?type=upjong&no=278'>반도체와반도체장비</a><span class='bar'>｜</span>재무정보: 2025.09 분기 기준)"
    assert _extract_naver_wics_label(html) == "반도체와반도체장비"


def test_extract_wiseindex_sector_label_prefers_sec_name() -> None:
    row = {"SEC_NM_KOR": "에너지", "IDX_NM_KOR": "WICS 에너지"}
    assert _extract_wiseindex_sector_label(row, "G1010") == "에너지"


def test_build_constituents_raises_when_no_local_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise(*args: object, **kwargs: object) -> list[dict[str, str]]:
        _ = (args, kwargs)
        raise RuntimeError("boom")

    monkeypatch.setattr(kr, "_fetch_krx_constituents", _raise)
    options = CollectorOptions(
        indices=("kospi200",),
        as_of="2016-12-30",
        timeout=1.0,
        retries=1,
        throttle=0.0,
        force_naver_fail=True,
        max_null_rate=0.05,
        allow_local_fallback=False,
    )
    with pytest.raises(RuntimeError):
        _ = kr._build_constituents(indices=("kospi200",), as_of="2016-12-30", options=options)
