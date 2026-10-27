"""
Inspect SNAP Enhanced Spectral Diversity (ESD) metadata.
ESD (Enhanced Spectral Diversity) is used in Sentinel-1 TOPS processing to estimate and correct
 residual azimuth misregistration, particularly using burst overlap regions.
"""

from __future__ import annotations

import json
import logging
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from pathlib import Path

LOG = logging.getLogger(__name__)

ESD_TERMS = (
    "enhanced_spectral_diversity",
    "spectral_diversity",
    "esd",
    "azimuth_shift",
    "azimuth_offset",
    "azimuth_misregistration",
    "azimuth_correction",
    "residual_azimuth",
)


@dataclass(frozen=True)
class ESDMeasurement:
    path: str
    name: str
    value: str
    unit: str | None


@dataclass(frozen=True)
class ESDValidationResult:
    product: str
    status: str
    measurements: list[ESDMeasurement]
    warnings: list[str]


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _metadata_name(element: ET.Element) -> str:
    return (
        element.attrib.get("name")
        or element.attrib.get("NAME")
        or _local_name(element.tag)
    )


def _collect_measurements(root: ET.Element) -> list[ESDMeasurement]:
    measurements: list[ESDMeasurement] = []

    def walk(element: ET.Element, parents: tuple[str, ...]) -> None:
        name = _metadata_name(element)
        path = (*parents, name)

        text = (element.text or "").strip()
        value = element.attrib.get("value", text).strip()
        unit = element.attrib.get("unit")

        if value and any(term in "/".join(path).lower() for term in ESD_TERMS):
            measurements.append(
                ESDMeasurement(
                    path="/".join(path),
                    name=name,
                    value=value,
                    unit=unit,
                )
            )

        for child in element:
            walk(child, path)

    walk(root, ())
    return measurements


def validate_esd(
    product: Path,
    *,
    report_path: Path | None = None,
    require_metadata: bool = False,
) -> ESDValidationResult:
    """Inspect ESD diagnostics without assuming SNAP-specific thresholds.

    This function does not independently measure residual
    coregistration accuracy.
    """
    product = Path(product).resolve()

    if not product.is_file():
        raise FileNotFoundError(product)

    try:
        root = ET.parse(product).getroot()
    except ET.ParseError as exc:
        raise RuntimeError(f"Invalid SNAP metadata: {product}") from exc

    measurements = _collect_measurements(root)
    warnings: list[str] = []

    if not measurements:
        warnings.append("No ESD diagnostic values were identified in DIMAP metadata.")
    else:
        warnings.append(
            "ESD-related metadata was found, but its physical meaning, "
            "units, and residual accuracy have not been verified."
        )

    result = ESDValidationResult(
        product=str(product),
        status="INSUFFICIENT_EVIDENCE",
        measurements=measurements,
        warnings=warnings,
    )

    if report_path is not None:
        report_path = Path(report_path)
        report_path.parent.mkdir(parents=True, exist_ok=True)

        report_path.write_text(
            json.dumps(
                asdict(result),
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    LOG.info(
        "ESD metadata inspection | product=%s | measurements=%d",
        product.name,
        len(measurements),
    )

    for warning in warnings:
        LOG.warning("%s", warning)

    if require_metadata and not measurements:
        raise RuntimeError(
            "No ESD diagnostics found. Inspect SNAP processing logs "
            "or product metadata before accepting registration."
        )

    return result
