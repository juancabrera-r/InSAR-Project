"""
Acquisition pipeline for processing selected Sentinel-1 acquisitions.

"""

from dataclasses import dataclass
from datetime import datetime
from logging import Logger
from pathlib import Path
from typing import Any

from insar_platform.geometry.metadata_validation import MetadataValidator
from insar_platform.ingestion.annotation import (
    AnnotationBurst,
    extract_annotation_bursts,
    extract_image_sampling_metadata,
)
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
) -> list[AcquisitionResult]:

    # Select the two earliest acquisitions from each product
    selected_pair = select_earliest_acquisitions(
        catalog.bursts_by_product,
        count=2,
    )

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

    # Inspect the catalogue's parent-product metadata (not the SAFE)
    if selected_pair is None or len(selected_pair) == 0:
        raise ValueError("No acquisitions selected from the catalog.")

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

        annotation_bursts = extract_annotation_bursts(xml_file_path=xml_file_path)

        burst_mapping = validation_metadata.map_catalogue_bursts_to_annotation(
            catalogue_bursts=catalog.bursts_by_product[product_id],
            annotation_bursts=annotation_bursts,
        )

        logger.info(
            "Product %s | Burst mapping: %s",
            product_id,
            burst_mapping,
        )

        catalogue_burst_id = config["CATALOGUE_BURST_ID"]

        if burst_mapping[catalogue_burst_id] is None:
            raise ValueError(
                f"No annotation found for catalogue burst {catalogue_burst_id} in product {product_id}"
            )

        annotation_index = burst_mapping[catalogue_burst_id]
        selected_burst = annotation_bursts[annotation_index]

        logger.info(
            "Product %s | Burst %s | Azimuth start time: %s",
            product_id,
            catalogue_burst_id,
            selected_burst["azimuth_time"],
        )

        first_valid_samples = selected_burst["first_valid_samples"]
        last_valid_samples = selected_burst["last_valid_samples"]

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
                selected_burst["azimuth_time"],
                next_burst["azimuth_time"],
            )

        sampling_metadata = extract_image_sampling_metadata(xml_file_path)

        logger.info(
            "Product %s | Sampling metadata: %s",
            product_id,
            sampling_metadata,
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

    return results
