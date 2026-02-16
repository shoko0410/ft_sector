# pyright: reportMissingImports=false,reportMissingTypeStubs=false,reportUnknownVariableType=false,reportUnknownMemberType=false,reportUnknownArgumentType=false,reportPrivateUsage=false
import pandas as pd
import pytest

from sector.collect import ft
from sector.collect.ft import _extract_last_page
from sector.collect.ft import _canonical_stock_code
from sector.collect.ft import _build_us_iwv_holdings_url
from sector.collect.ft import _collect_us_russell3000_from_iwv
from sector.collect.ft import _extract_tearsheet_taxonomy
from sector.collect.ft import _map_us_exchange_name_to_ft_codes
from sector.collect.ft import _parse_us_iwv_holdings_csv
from sector.collect.ft import _parse_ajax_rows
from sector.collect.ft import _split_ft_ticker
from sector.collect.ft import _target_rows_for_scope


def test_target_rows_for_scope_uses_known_universe() -> None:
    assert _target_rows_for_scope("russell3000", 10) == 3000
    assert _target_rows_for_scope("topix500", 10) == 500
    assert _target_rows_for_scope("unknown", 10) == 10


def test_parse_ajax_rows_extracts_equity_row() -> None:
    fragment = (
        '<tr><td class="mod-ui-table__cell--text">'
        '<a href="/data/equities/tearsheet/summary?s=AAPL:NSQ" class="mod-ui-link">'
        '<span class="mod-ui-hide-xsmall">Apple Inc</span>'
        '<span class="mod-ui-hide-small-above">AAPL:NSQ</span></a></td>'
        '<td class="mod-ui-table__cell--text">United States</td>'
        '<td class="mod-ui-table__cell--text">Technology Hardware and Equipment</td></tr>'
    )
    parsed = _parse_ajax_rows(fragment)
    assert len(parsed) == 1
    assert parsed[0]["ticker"] == "AAPL:NSQ"
    assert parsed[0]["stock_code"] == "AAPL"
    assert parsed[0]["stock_name"] == "Apple Inc"
    assert parsed[0]["country_label"] == "United States"
    assert parsed[0]["native_taxonomy_label"] == "Technology Hardware and Equipment"
    assert parsed[0]["exchange_code"] == "NSQ"


def test_parse_ajax_rows_handles_three_part_ticker() -> None:
    fragment = (
        '<tr><td class="mod-ui-table__cell--text">'
        '<a href="/data/equities/tearsheet/summary?s=AADR:NMQ:USD" class="mod-ui-link">'
        '<span class="mod-ui-hide-xsmall">Sample ADR Fund</span>'
        '<span class="mod-ui-hide-small-above">AADR:NMQ:USD</span></a></td>'
        '<td class="mod-ui-table__cell--text">United States</td>'
        '<td class="mod-ui-table__cell--text">--</td></tr>'
    )
    parsed = _parse_ajax_rows(fragment)
    assert len(parsed) == 1
    assert parsed[0]["stock_code"] == "AADR"
    assert parsed[0]["exchange_code"] == "NMQ"


def test_split_ft_ticker_picks_exchange_segment() -> None:
    assert _split_ft_ticker("AAPL:NSQ") == ("AAPL", "NSQ")
    assert _split_ft_ticker("AADR:NMQ:USD") == ("AADR", "NMQ")
    assert _split_ft_ticker("INVALID") is None


def test_map_us_exchange_name_to_ft_codes() -> None:
    assert "NSQ" in _map_us_exchange_name_to_ft_codes("NASDAQ")
    assert "NYQ" in _map_us_exchange_name_to_ft_codes("NEW YORK STOCK EXCHANGE INC")


def test_canonical_stock_code_normalizes_share_class_symbols() -> None:
    assert _canonical_stock_code("BRK.B") == "BRKB"
    assert _canonical_stock_code("BRK/B") == "BRKB"


def test_extract_tearsheet_taxonomy_from_overview_block() -> None:
    html = (
        '<div class="mod-tearsheet-overview__esi">'
        'Consumer Discretionary'
        '<i class="o-ft-icons-icon o-ft-icons-icon--arrow-right"></i>'
        'Consumer Services '
        '</div>'
    )
    taxonomy = _extract_tearsheet_taxonomy(html)
    assert taxonomy == ("Consumer Discretionary", "Consumer Services")


def test_extract_last_page_reads_pagination_numbers() -> None:
    fragment = (
        '<button class="o-buttons mod-ui-pagination__number" data-mod-pagination-num="1" type="button">1</button>'
        '<button class="o-buttons mod-ui-pagination__number" data-mod-pagination-num="2" type="button">2</button>'
        '<button class="o-buttons mod-ui-pagination__number" data-mod-pagination-num="150" type="button">150</button>'
    )
    assert _extract_last_page(fragment, current_page=1) == 150


def test_build_us_iwv_holdings_url_includes_as_of_date() -> None:
    url = _build_us_iwv_holdings_url("20160331")
    assert "asOfDate=20160331" in url
    assert "fileName=IWV_holdings" in url


def test_parse_us_iwv_holdings_csv_reads_table_after_metadata() -> None:
    raw_csv = (
        "Fund Holdings as of,\"Mar 31, 2016\"\n"
        "Some Header,Value\n"
        "Ticker,Name,Sector,Asset Class,Market Value,Weight (%),Notional Value,Quantity,CUSIP,ISIN,SEDOL,Price,Location,Exchange,Currency,FX Rate,Accrual Date\n"
        "AAPL,Apple Inc,Information Technology,Equity,1,2,3,4,5,6,7,8,United States,NASDAQ,USD,1,\n"
    )
    frame = _parse_us_iwv_holdings_csv(raw_csv)
    assert list(frame.columns)[:4] == ["Ticker", "Name", "Sector", "Asset Class"]
    assert str(frame.iloc[0]["Ticker"]) == "AAPL"


def test_collect_us_russell3000_from_iwv_builds_constituent_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    sample = pd.DataFrame(
        [
            {
                "Ticker": "AAPL",
                "Name": "Apple Inc",
                "Sector": "Information Technology",
                "Asset Class": "Equity",
                "Exchange": "NASDAQ",
                "Location": "United States",
            }
        ]
    )

    def _fake_fetch(options: ft.CollectorOptions) -> tuple[pd.DataFrame, str]:
        _ = options
        return sample, "https://example.test/iwv.csv?asOfDate=20160331"

    monkeypatch.setattr(ft, "_fetch_us_iwv_equity_rows", _fake_fetch)

    options = ft.CollectorOptions(
        market="us",
        as_of="2016-03-31",
        parser="js-engine",
        universe_scope="russell3000",
        target_rows=3000,
        timeout=10.0,
        retries=1,
        throttle=0.0,
        force=False,
    )
    frame, stats = _collect_us_russell3000_from_iwv(options)
    assert int(len(frame)) == 1
    assert str(frame.iloc[0]["ticker"]).startswith("AAPL:")
    assert str(frame.iloc[0]["native_taxonomy_level1"]) == "Technology"
    assert str(stats["membership_source"]).startswith("https://example.test")
