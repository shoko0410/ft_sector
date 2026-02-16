# pyright: reportMissingImports=false,reportUnknownVariableType=false,reportUnknownMemberType=false
from sector.pipeline import build


def test_parse_universes_accepts_plan_shape() -> None:
    parsed = build._parse_universes("us:russell3000 kr:kospi200,kosdaq150 jp:topix500")
    assert parsed["us"] == "russell3000"
    assert parsed["kr"] == "kospi200,kosdaq150"
    assert parsed["jp"] == "topix500"


def test_main_requires_primary_listing_only() -> None:
    code = build.main([
        "--as-of",
        "2026-01-31",
        "--universes",
        "us:russell3000 kr:kospi200,kosdaq150 jp:topix500",
        "--kr-proxy-level",
        "level1,level3",
    ])
    assert code == 1
