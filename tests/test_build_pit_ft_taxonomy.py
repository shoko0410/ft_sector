# pyright: reportMissingImports=false,reportUnknownVariableType=false
from sector.transform.build_pit import _resolve_ft_taxonomy


def test_resolve_ft_taxonomy_uses_default_level3_for_known_level1() -> None:
    level1, level3, is_fallback = _resolve_ft_taxonomy(level1_raw="Health Care", level3_raw="--")
    assert level1 == "Health Care"
    assert level3 == "Pharmaceuticals and Biotechnology"
    assert is_fallback is True


def test_resolve_ft_taxonomy_keeps_native_level3_when_present() -> None:
    level1, level3, is_fallback = _resolve_ft_taxonomy(
        level1_raw="Consumer Discretionary",
        level3_raw="Consumer Services",
    )
    assert level1 == "Consumer Discretionary"
    assert level3 == "Consumer Services"
    assert is_fallback is False
