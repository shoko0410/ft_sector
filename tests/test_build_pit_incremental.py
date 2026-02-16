# pyright: reportMissingImports=false,reportMissingTypeStubs=false,reportUnknownVariableType=false,reportUnknownMemberType=false,reportUnknownArgumentType=false
import pandas as pd

from sector.transform.build_pit import _build_incremental_history


def test_build_incremental_history_closes_dropped_security() -> None:
    existing = pd.DataFrame(
        [
            {"security_id": "KRX:000001", "effective_from": "2026-01-01", "effective_to": None, "is_current": True},
            {"security_id": "KRX:000002", "effective_from": "2026-01-01", "effective_to": None, "is_current": True},
        ]
    )
    incoming = pd.DataFrame(
        [
            {"security_id": "KRX:000001", "effective_from": "2026-01-31", "effective_to": None, "is_current": True},
        ]
    )

    history = _build_incremental_history(existing=existing, incoming=incoming, as_of="2026-01-31")

    closed = history[(history["security_id"] == "KRX:000002") & (~history["is_current"].astype(bool))]
    assert len(closed) == 1
    assert str(closed.iloc[0]["effective_to"]) == "2026-01-31"

    current = history[history["is_current"].astype(bool)]
    assert set(current["security_id"].astype(str).tolist()) == {"KRX:000001"}
