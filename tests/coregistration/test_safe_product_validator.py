"""Tests for Sentinel-1 SAFE manifest validation."""

from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

MANIFEST_DIRECTORY = Path("data") / "raw" / "manifest"

PRODUCT_IDS = [
    "4770594e-9ee5-432a-9552-22f1e27efd61",
    "216aff3b-f44e-4d82-9e71-f28a43fce10d",
]


@pytest.mark.parametrize("product_id", PRODUCT_IDS)
def test_manifest_exists_and_is_valid_xml(
    product_id: str,
) -> None:
    """Verify that each downloaded manifest exists and parses."""

    manifest_path = MANIFEST_DIRECTORY / product_id / "manifest.safe"

    assert manifest_path.is_file(), f"Manifest not found: {manifest_path}"

    assert manifest_path.stat().st_size > 0, f"Manifest is empty: {manifest_path}"

    tree = ET.parse(manifest_path)
    root = tree.getroot()

    assert root.tag.split("}")[-1] == "XFDU", (
        f"Unexpected SAFE manifest root element: {root.tag}"
    )
