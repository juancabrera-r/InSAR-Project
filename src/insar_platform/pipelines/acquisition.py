"""
Acquisition pipeline for processing selected Sentinel-1 acquisitions.

"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from logging import Logger
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

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

    # Select the two earliest acquisitions from each product
    selected_pair = select_earliest_acquisitions(
        catalog.bursts_by_product,
        count=2,
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

        # 2. Retrieve this product's nodes
        nodes = cdse_ingestion.get_product_nodes(product_id=product_id)

        children_node = cdse_ingestion.get_children_node(product_nodes=nodes)

        annotation_files = cdse_ingestion.get_directory_files(
            children_node=children_node,
            directory_name="annotation",
        )

        # 3. Retrieve measurement files
        measurement_files = cdse_ingestion.get_directory_files(
            children_node=children_node,
            directory_name="measurement",
        )

        iw3_vv_measurements = [
            node
            for node in measurement_files["result"]
            if node["Name"].lower().startswith("s1a-iw3-slc-vv-")
            and node["Name"].lower().endswith(".tiff")
        ]

        if len(iw3_vv_measurements) != 1:
            raise ValueError(
                f"Expected one IW3 VV measurement for {product_id}, "
                f"found {len(iw3_vv_measurements)}"
            )

        measurement_node = iw3_vv_measurements[0]

        logger.debug(
            "Product %s | Selected measurement: %s",
            product_id,
            measurement_node["Name"],
        )

        # 3. Download the measurement file
        # Download the selected IW3 VV measurement for each acquisition.
        measurement_path = (
            Path(config["MEASUREMENT_DATA_RAW"]) / product_id / measurement_node["Name"]
        )

        measurement_path.parent.mkdir(parents=True, exist_ok=True)

        if measurement_path.is_file() and measurement_path.stat().st_size > 0:
            logger.info(
                "Measurement already exists: %s",
                measurement_path,
            )
        else:
            logger.info(
                "Downloading IW3 VV measurement for product %s: %s",
                product_id,
                measurement_node["Name"],
            )

            cdse_ingestion.download_measurement_file(
                measurement_node=measurement_node,
                destination_path=measurement_path,
            )

        logger.info(
            "Product %s | Measurement size: %.2f GiB",
            product_id,
            measurement_path.stat().st_size / (1024**3),
        )

        # 3. Select the IW3 VV annotation
        iw3_vv_files = [
            node
            for node in annotation_files["result"]
            if node["Name"].lower().startswith("s1a-iw3-slc-vv-")
            and node["Name"].lower().endswith(".xml")
        ]

        if len(iw3_vv_files) != 1:
            raise ValueError(
                f"Expected one IW3 VV annotation for {product_id}, "
                f"found {len(iw3_vv_files)}"
            )

        annotation_node = iw3_vv_files[0]

        # 4. Download the annotation
        xml_file_path = (
            Path(config["ANNOTATION_DATA_RAW"]) / product_id / annotation_node["Name"]
        )

        xml_file_path.parent.mkdir(parents=True, exist_ok=True)

        cdse_ingestion.download_annotation_file(
            annotation_node=annotation_node,
            destination_path=xml_file_path,
        )
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
