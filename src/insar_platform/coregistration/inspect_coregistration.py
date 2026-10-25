"""Sentinel-1 inspect coregistration preparation pipeline."""

from dataclasses import dataclass
from logging import Logger
from pathlib import Path

from insar_platform.coregistration.safe_manifest import (
    SafeManifestInspector,
    SafeManifestReport,
)
from insar_platform.pipelines.acquisition import AcquisitionResult


@dataclass(frozen=True)
class CoregistrationPreparationResult:
    """SAFE manifest inspection results for the selected acquisitions."""

    reports: tuple[SafeManifestReport, ...]
    total_references: int
    total_available: int
    total_missing: int


def inspect_coregistration_pipeline(
    acquisitions: list[AcquisitionResult],
    raw_directory: Path,
    logger: Logger,
) -> CoregistrationPreparationResult:
    """Inspect SAFE dependencies before SNAP coregistration."""

    if len(acquisitions) != 2:
        raise ValueError(
            "Coregistration requires exactly two acquisitions, "
            f"received {len(acquisitions)}."
        )

    inspector = SafeManifestInspector()
    reports: list[SafeManifestReport] = []

    for acquisition in acquisitions:
        product_id = acquisition.product_id

        manifest_path = raw_directory / "manifest" / product_id / "manifest.safe"

        report = inspector.inspect(
            manifest_path=manifest_path,
            annotation_directory=acquisition.annotation_path.parent,
            measurement_directory=acquisition.measurement_path.parent,
        )

        reports.append(report)

        logger.info(
            "Product %s | SAFE references: %d | Available: %d | Missing: %d",
            product_id,
            len(report.references),
            len(report.available),
            len(report.missing),
        )

        for reference in report.missing:
            logger.warning(
                "Product %s | Missing SAFE dependency [%s]: %s",
                product_id,
                reference.category,
                reference.relative_path,
            )

    result = CoregistrationPreparationResult(
        reports=tuple(reports),
        total_references=sum(len(report.references) for report in reports),
        total_available=sum(len(report.available) for report in reports),
        total_missing=sum(len(report.missing) for report in reports),
    )

    logger.info(
        "Coregistration preparation completed | "
        "References: %d | Available: %d | Missing: %d",
        result.total_references,
        result.total_available,
        result.total_missing,
    )

    return result
