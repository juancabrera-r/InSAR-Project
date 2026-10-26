"""Sentinel-1 inspect coregistration preparation pipeline."""

from dataclasses import dataclass
from logging import Logger
from pathlib import Path
from xml.etree import ElementTree as ET
from zipfile import BadZipFile, ZipFile

from insar_platform.pipelines.acquisition import AcquisitionResult


@dataclass(frozen=True)
class CoregistrationPreparationResult:
    """SAFE ZIP inspection results for selected acquisitions."""

    reports: tuple
    total_references: int
    total_available: int
    total_missing: int


@dataclass(frozen=True)
class SafeZipReport:
    product_id: str
    zip_path: Path
    references: tuple[str, ...]
    available: tuple[str, ...]
    missing: tuple[str, ...]


def inspect_safe_zip(
    product_id: str,
    zip_path: Path,
) -> SafeZipReport:
    """Validate manifest dependencies inside a complete SAFE ZIP."""

    if not zip_path.is_file():
        raise FileNotFoundError(f"SAFE ZIP not found: {zip_path}")

    with ZipFile(zip_path) as archive:
        corrupt_member = archive.testzip()

        if corrupt_member is not None:
            raise BadZipFile(f"Corrupted SAFE ZIP member: {corrupt_member}")

        members = {
            name.replace("\\", "/").lstrip("./")
            for name in archive.namelist()
            if not name.endswith("/")
        }

        manifests = [name for name in members if name.endswith("/manifest.safe")]

        if len(manifests) != 1:
            raise ValueError(
                f"Expected one manifest.safe in {zip_path}, found {len(manifests)}"
            )

        manifest_name = manifests[0]
        safe_root = manifest_name.rsplit("/", 1)[0]

        root = ET.fromstring(archive.read(manifest_name))

        references = []

        for element in root.iter():
            href = element.attrib.get("href")

            if not href:
                continue

            relative_path = href.replace("\\", "/").removeprefix("./")

            if relative_path.startswith(
                ("annotation/", "measurement/", "preview/", "support/")
            ):
                references.append(relative_path)

        references = sorted(set(references))

        available = [ref for ref in references if f"{safe_root}/{ref}" in members]

        missing = [ref for ref in references if f"{safe_root}/{ref}" not in members]

    return SafeZipReport(
        product_id=product_id,
        zip_path=zip_path,
        references=tuple(references),
        available=tuple(available),
        missing=tuple(missing),
    )


def inspect_coregistration_pipeline(
    acquisitions: list[AcquisitionResult],
    raw_directory: Path,
    logger: Logger,
) -> CoregistrationPreparationResult:
    """Inspect selected SAFE ZIPs before SNAP coregistration."""

    if len(acquisitions) < 2:
        raise ValueError(
            "Coregistration requires at least two acquisitions, "
            f"received {len(acquisitions)}."
        )

    reports = []

    for acquisition in acquisitions:
        zip_path = (
            raw_directory
            / "safe"
            / f"{acquisition.product_name.removesuffix('.SAFE')}.SAFE.zip"
        )

        report = inspect_safe_zip(
            product_id=acquisition.product_id,
            zip_path=zip_path,
        )

        reports.append(report)

        logger.info(
            "Product %s | SAFE ZIP: %s | References: %d | Available: %d | Missing: %d",
            acquisition.product_id,
            zip_path,
            len(report.references),
            len(report.available),
            len(report.missing),
        )

        for reference in report.missing:
            logger.warning(
                "Product %s | Missing SAFE dependency: %s",
                acquisition.product_id,
                reference,
            )

    result = CoregistrationPreparationResult(
        reports=tuple(reports),
        total_references=sum(len(r.references) for r in reports),
        total_available=sum(len(r.available) for r in reports),
        total_missing=sum(len(r.missing) for r in reports),
    )

    logger.info(
        "Coregistration preparation completed | "
        "References: %d | Available: %d | Missing: %d",
        result.total_references,
        result.total_available,
        result.total_missing,
    )

    return result
