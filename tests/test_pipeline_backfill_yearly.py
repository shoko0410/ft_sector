# pyright: reportMissingImports=false,reportPrivateUsage=false
import pytest

from sector.pipeline import backfill_yearly


def test_build_as_of_constructs_iso_date() -> None:
    assert backfill_yearly._build_as_of(2016, "12-31") == "2016-12-31"


def test_build_as_of_rejects_invalid_month_day() -> None:
    with pytest.raises(ValueError):
        _ = backfill_yearly._build_as_of(2016, "13-31")


def test_kr_indices_for_year_applies_kosdaq150_start_year() -> None:
    assert backfill_yearly._kr_indices_for_year(2014) == "kospi200"
    assert backfill_yearly._kr_indices_for_year(2015) == "kospi200,kosdaq150"
