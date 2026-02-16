# pyright: reportMissingImports=false,reportUnknownVariableType=false
from sector.collect.kr import _extract_naver_wics_label


def test_extract_naver_wics_label_wics_pattern() -> None:
    html = "<em>WICS :</em><a href='#'>반도체와반도체장비</a>"
    assert _extract_naver_wics_label(html) == "반도체와반도체장비"


def test_extract_naver_wics_label_industry_name_pattern() -> None:
    html = "동종업종비교(업종명 : <a href='/sise/sise_group_detail.naver?type=upjong&no=278'>반도체와반도체장비</a><span class='bar'>｜</span>재무정보: 2025.09 분기 기준)"
    assert _extract_naver_wics_label(html) == "반도체와반도체장비"
