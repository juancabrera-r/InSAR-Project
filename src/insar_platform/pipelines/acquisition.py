"""
Acquisition pipeline for processing selected Sentinel-1 acquisitions.

"""

import json
import shutil
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from logging import Logger
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET
from zipfile import BadZipFile, ZipFile

import requests

from insar_platform.app.validations.sar_measurement_validator import (
    SARMeasurementValidator,
)
from insar_platform.database.models.sar_product_file import SARFileType
from insar_platform.database.services.sar_product_file import (
    SARProductFileRecord,
    register_sar_product_files,
)
from insar_platform.geometry.calculate_orbit import CalculateOrbit
from insar_platform.geometry.metadata_validation import MetadataValidator
from insar_platform.ingestion.annotation import AnnotationBurst, AnnotationManager
from insar_platform.ingestion.cdse import CDSEIngestion
from insar_platform.ingestion.metadata import select_earliest_acquisitions
from insar_platform.pipelines.catalog import CatalogDiscoveryResult


@dataclass
class AcquisitionResult:
    product_id: str
    product_name: str
    acquisition_time: datetime
    measurement_path: Path
    annotation_path: Path
    manifest_path: Path
    annotation_bursts: list[AnnotationBurst]
    burst_mapping: dict[int, int]
    sampling_metadata: Any


def acquisition_pipeline(
    catalog: CatalogDiscoveryResult,
    config: dict,
    env: dict,
    logger: Logger,
    run_id: uuid.UUID | None = None,
) -> list[AcquisitionResult]:

    # Select acquisitions: manual count or all available.
    acquisition_count = config.get("ACQUISITION_COUNT")

    if acquisition_count is None:
        # Automatic mode: select all available products.
        acquisition_count = len(catalog.bursts_by_product)
        logger.info(
            "Automatic acquisition selection: %d available products",
            acquisition_count,
        )
    else:
        # Manual mode: select the requested number.
        acquisition_count = int(acquisition_count)

        if acquisition_count < 1:
            raise ValueError("ACQUISITION_COUNT must be >= 1 or null")

        logger.info(
            "Manual acquisition selection: requesting %d acquisitions",
            acquisition_count,
        )

    selected_pair = select_earliest_acquisitions(
        catalog.bursts_by_product,
        count=acquisition_count,
    )

    logger.info(
        "Selected %d acquisitions",
        len(selected_pair),
    )

    if not selected_pair:
        raise ValueError("No acquisitions selected from the catalog.")

    results: list[AcquisitionResult] = []

    # Temporary: Log all bursts for the selected acquisitions
    for acquisition in selected_pair:
        product_id = acquisition["product_id"]

        product_bursts = sorted(
            catalog.bursts_by_product[product_id],
            key=lambda burst: burst["BurstId"],
        )

        for burst in product_bursts:
            logger.info(
                "Product=%s | BurstId=%s | AzimuthTime=%s | Start=%s | End=%s",
                product_id,
                burst["BurstId"],
                burst["AzimuthTime"],
                burst["ContentDate"]["Start"],
                burst["ContentDate"]["End"],
            )

    for acquisition in selected_pair:
        logger.info(
            "Selected SLC: %s | Date: %s | Product ID: %s",
            acquisition["product_name"],
            acquisition["acquisition_time"],
            acquisition["product_id"],
        )

    cdse_ingestion = CDSEIngestion(
        config=config,
        selected_pair=selected_pair,
        env=env,
        logger=logger,
    )

    validation_metadata = MetadataValidator(
        bursts_by_product=catalog.bursts_by_product,
        config=config,
        logger=logger,
    )

    for acquisition in selected_pair:
        product_id = acquisition["product_id"]

        logger.info(
            "Processing acquisition: %s | Product ID: %s",
            acquisition["product_name"],
            product_id,
        )

        # 1. Retrieve metadata for this acquisition
        product_metadata = cdse_ingestion.get_product_metadata(product_id=product_id)

        logger.debug(
            "Product: %s | Size: %.2f GiB | Online: %s",
            product_metadata["Name"],
            product_metadata["ContentLength"] / (1024**3),
            product_metadata["Online"],
        )

        # Download the complete original SAFE product as one ZIP archive.
        safe_directory = Path(config["ANNOTATION_DATA_RAW"]).parent / "safe"
        safe_directory.mkdir(parents=True, exist_ok=True)
        product_name = product_metadata["Name"].removesuffix(".zip")
        if not product_name.endswith(".SAFE"):
            product_name += ".SAFE"
        safe_zip_path = safe_directory / f"{product_name}.zip"

        def inspect_safe_archive(
            archive: ZipFile,
            product_id: str,
        ) -> tuple[str, str, str]:

            members = [name for name in archive.namelist() if not name.endswith("/")]

            manifest_files = [
                name for name in members if name.endswith("/manifest.safe")
            ]

            annotation_files = [
                name
                for name in members
                if "/annotation/" in name
                and name.rsplit("/", 1)[-1].lower().startswith("s1a-iw3-slc-vv-")
                and name.lower().endswith(".xml")
            ]

            measurement_files = [
                name
                for name in members
                if "/measurement/" in name
                and name.rsplit("/", 1)[-1].lower().startswith("s1a-iw3-slc-vv-")
                and name.lower().endswith((".tif", ".tiff"))
            ]

            if not (
                len(manifest_files)
                == len(annotation_files)
                == len(measurement_files)
                == 1
            ):
                raise ValueError(
                    f"Unexpected SAFE contents for {product_id}: "
                    f"manifest={len(manifest_files)}, "
                    f"IW3 VV annotations={len(annotation_files)}, "
                    f"IW3 VV measurements={len(measurement_files)}"
                )
            return manifest_files[0], annotation_files[0], measurement_files[0]

        archive_ready = False
        verification_path = safe_zip_path.with_suffix(".zip.verified.json")

        if safe_zip_path.is_file():
            try:
                stat = safe_zip_path.stat()
                previously_verified = False

                if verification_path.is_file():
                    try:
                        verification = json.loads(
                            verification_path.read_text(encoding="utf-8")
                        )
                        previously_verified = (
                            verification.get("size") == stat.st_size
                            and verification.get("mtime_ns") == stat.st_mtime_ns
                        )
                    except (OSError, ValueError, TypeError):
                        pass

                with ZipFile(safe_zip_path) as archive:
                    inspect_safe_archive(archive, product_id)

                    if not previously_verified:
                        logger.info(
                            "Performing full SAFE ZIP integrity check: %s",
                            safe_zip_path,
                        )
                        if archive.testzip() is not None:
                            raise BadZipFile("CRC check failed")

                archive_ready = True

                if not previously_verified:
                    stat = safe_zip_path.stat()
                    verification_path.write_text(
                        json.dumps(
                            {
                                "size": stat.st_size,
                                "mtime_ns": stat.st_mtime_ns,
                            }
                        ),
                        encoding="utf-8",
                    )

            except (BadZipFile, ValueError, OSError):
                archive_ready = False
                verification_path.unlink(missing_ok=True)
                logger.warning(
                    "Invalid cached SAFE ZIP, downloading again: %s",
                    safe_zip_path,
                )

        if not archive_ready:
            temporary_path = safe_zip_path.with_suffix(".zip.part")
            download_url = (
                "https://download.dataspace.copernicus.eu/odata/v1/"
                f"Products({product_id})/$value"
            )
            try:
                with requests.get(
                    download_url,
                    headers={
                        "Authorization": f"Bearer {cdse_ingestion.authenticate()}"
                    },
                    stream=True,
                    timeout=(30, 180),
                ) as response:
                    response.raise_for_status()

                    with temporary_path.open("wb") as output:
                        for chunk in response.iter_content(chunk_size=1024 * 1024):
                            if chunk:
                                output.write(chunk)

                with ZipFile(temporary_path) as archive:
                    inspect_safe_archive(archive, product_id)
                    if archive.testzip() is not None:
                        raise BadZipFile("CRC check failed")

                temporary_path.replace(safe_zip_path)

                stat = safe_zip_path.stat()

                verification_path.write_text(
                    json.dumps(
                        {
                            "size": stat.st_size,
                            "mtime_ns": stat.st_mtime_ns,
                        }
                    ),
                    encoding="utf-8",
                )
            except Exception:
                temporary_path.unlink(missing_ok=True)
                raise
            logger.info("Downloaded complete SAFE archive: %s", safe_zip_path)
        else:
            logger.info("Using cached SAFE archive: %s", safe_zip_path)

        # Extract only the files required by the existing Python validators.
        # The complete original SAFE remains available for SNAP in safe_zip_path.
        annotation_directory = Path(config["ANNOTATION_DATA_RAW"]) / product_id
        measurement_directory = Path(config["MEASUREMENT_DATA_RAW"]) / product_id
        manifest_path = (
            Path(config["ANNOTATION_DATA_RAW"]).parent
            / "manifest"
            / product_id
            / "manifest.safe"
        )

        with ZipFile(safe_zip_path) as archive:
            manifest_member, annotation_member, measurement_member = (
                inspect_safe_archive(archive, product_id)
            )
            xml_file_path = annotation_directory / Path(annotation_member).name
            measurement_path = measurement_directory / Path(measurement_member).name

            for member, destination in (
                (manifest_member, manifest_path),
                (annotation_member, xml_file_path),
                (measurement_member, measurement_path),
            ):
                member_info = archive.getinfo(member)
                if (
                    destination.is_file()
                    and destination.stat().st_size == member_info.file_size
                ):
                    continue
                destination.parent.mkdir(parents=True, exist_ok=True)
                temporary_path = destination.with_name(destination.name + ".part")
                try:
                    with (
                        archive.open(member) as source,
                        temporary_path.open("wb") as target,
                    ):
                        shutil.copyfileobj(source, target, length=1024 * 1024)
                    temporary_path.replace(destination)
                except Exception:
                    temporary_path.unlink(missing_ok=True)
                    raise

        ET.parse(manifest_path)
        logger.info("Product %s | SAFE ZIP: %s", product_id, safe_zip_path)

        annotation_manager = AnnotationManager(
            annotation_path=xml_file_path,
            logger=logger,
        )
        root = ET.parse(xml_file_path).getroot()

        annotation_bursts, lines_per_burst, _ = (
            annotation_manager.extract_annotation_bursts(root=root)
        )

        sampling_metadata = annotation_manager.extract_image_sampling_metadata(
            root=root
        )

        burst_mapping = validation_metadata.map_catalogue_bursts_to_annotation(
            catalogue_bursts=catalog.bursts_by_product[product_id],
            annotation_bursts=annotation_bursts,
        )

        logger.info(
            "Product %s | Burst mapping: %s",
            product_id,
            burst_mapping,
        )

        catalogue_burst_id = int(config["CATALOGUE_BURST_ID"])

        annotation_index = burst_mapping.get(catalogue_burst_id)

        if annotation_index is None:
            raise ValueError(
                f"No annotation found for catalogue burst "
                f"{catalogue_burst_id} in product {product_id}"
            )

        if not 0 <= annotation_index < len(annotation_bursts):
            raise ValueError(
                f"Invalid annotation index {annotation_index} "
                f"for catalogue burst {catalogue_burst_id}"
            )

        selected_burst = annotation_bursts[annotation_index]

        burst_start = datetime.fromisoformat(selected_burst.azimuth_time).astimezone(
            UTC
        )

        burst_end = burst_start + timedelta(
            seconds=(len(selected_burst.first_valid_samples) - 1)
            * sampling_metadata.azimuth_time_interval
        )

        calculate_orbit = CalculateOrbit(logger)

        satellite = calculate_orbit.extract_satellite(xml_file_path=xml_file_path)

        cdse_ingestion.download_orbit_for_acquisition(
            satellite=satellite,
            burst_start=burst_start,
            burst_end=burst_end,
            destination_directory=Path(config["ORBIT_DATA_RAW"]),
        )

        logger.info(
            "Product %s | Burst %s | Azimuth start time: %s",
            product_id,
            catalogue_burst_id,
            selected_burst.azimuth_time,
        )

        first_valid_samples = selected_burst.first_valid_samples
        last_valid_samples = selected_burst.last_valid_samples

        logger.info(
            "Product %s | Burst %s | Annotation index %s | Validity entries: %s",
            product_id,
            catalogue_burst_id,
            annotation_index,
            len(first_valid_samples),
        )

        valid_line_count = sum(
            first >= 0 and last >= first
            for first, last in zip(
                first_valid_samples,
                last_valid_samples,
            )
        )

        logger.info(
            "Product %s | Burst %s | Valid azimuth lines: %s / %s",
            product_id,
            catalogue_burst_id,
            valid_line_count,
            len(first_valid_samples),
        )

        if annotation_index + 1 < len(annotation_bursts):
            next_burst = annotation_bursts[annotation_index + 1]

            logger.info(
                "Product %s | Burst %s start: %s | Next burst start: %s",
                product_id,
                catalogue_burst_id,
                selected_burst.azimuth_time,
                next_burst.azimuth_time,
            )

        logger.info(
            "Product %s | Sampling metadata: %s",
            product_id,
            sampling_metadata,
        )

        # Validate the selected annotation burst against the measurement TIFF.
        validator = SARMeasurementValidator(
            measurement_path=measurement_path,
            logger=logger,
            max_invalid_nonzero_fraction=config["MAX_INVALID_NONZERO_FRACTION"],
        )

        mask_validation = validator.compare_mask_with_tiff(
            annotation_bursts=annotation_bursts,
            burst_index=annotation_index,
            lines_per_burst=lines_per_burst,
        )

        logger.info(
            "Product %s | Burst %s | Mask validation: %s",
            product_id,
            catalogue_burst_id,
            mask_validation,
        )

        # Validate an adjacent burst boundary.
        if len(annotation_bursts) < 2:
            raise ValueError(
                f"Product {product_id}: insufficient bursts for boundary validation"
            )

        boundary_index = (
            annotation_index + 1
            if annotation_index + 1 < len(annotation_bursts)
            else annotation_index
        )

        boundary_result = validator.inspect_boundary(
            annotation_bursts=annotation_bursts,
            boundary_index=boundary_index,
            lines_per_burst=lines_per_burst,
        )

        logger.info(
            "Product %s | Boundary index %d | Result: %s",
            product_id,
            boundary_index,
            boundary_result,
        )

        results.append(
            AcquisitionResult(
                product_id=product_id,
                product_name=acquisition["product_name"],
                acquisition_time=acquisition["acquisition_time"],
                measurement_path=measurement_path,
                annotation_path=xml_file_path,
                manifest_path=manifest_path,
                annotation_bursts=annotation_bursts,
                burst_mapping=burst_mapping,
                sampling_metadata=sampling_metadata,
            )
        )

    # Register files after processing ALL acquisitions
    records = []

    for acquisition in results:
        records.extend(
            [
                SARProductFileRecord(
                    product_name=acquisition.product_name,
                    file_type=SARFileType.MEASUREMENT,
                    file_path=acquisition.measurement_path,
                ),
                SARProductFileRecord(
                    product_name=acquisition.product_name,
                    file_type=SARFileType.ANNOTATION,
                    file_path=acquisition.annotation_path,
                ),
            ]
        )

    inserted_count = register_sar_product_files(
        logger=logger,
        records=records,
        download_run_id=run_id,
    )

    logger.info(
        "Registered %d new SAR product files",
        inserted_count,
    )

    return results
