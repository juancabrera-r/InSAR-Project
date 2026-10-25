"""Tests for SNAP GPT integration."""

from pathlib import Path

import pytest

from insar_platform.coregistration.snap_adapter import SnapAdapter


@pytest.fixture
def snap_adapter() -> SnapAdapter:
    return SnapAdapter(
        gpt_path=Path(r"C:\Program Files\esa-snap\bin\gpt.exe"),
        timeout_seconds=3600,
    )


@pytest.mark.parametrize(
    "operator",
    [
        "TOPSAR-Split",
        "Apply-Orbit-File",
        "Back-Geocoding",
        "Enhanced-Spectral-Diversity",
    ],
)
def test_required_snap_operators(
    snap_adapter: SnapAdapter,
    operator: str,
) -> None:
    assert snap_adapter.check_operator(operator), (
        f"SNAP operator unavailable: {operator}"
    )
