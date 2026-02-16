# pyright: reportMissingImports=false,reportMissingTypeStubs=false,reportUnknownVariableType=false,reportUnknownMemberType=false
import pandas as pd

from sector.qa import check_schema
from sector.qa.compare_hash import _content_hash


def test_check_schema_parse_required() -> None:
    required = check_schema._parse_required("security_id,issuer_id,ticker")
    assert required == ["security_id", "issuer_id", "ticker"]


def test_compare_hash_content_hash_is_order_independent() -> None:
    left = pd.DataFrame([{"a": 1, "b": "x"}, {"a": 2, "b": "y"}])
    right = pd.DataFrame([{"b": "y", "a": 2}, {"b": "x", "a": 1}])
    assert _content_hash(left) == _content_hash(right)
