"""
Pipeline for discovering and validating Sentinel-1 catalog acquisitions within a specified Area of Interest (AOI).
"""

import logging
from dataclasses import dataclass
from typing import Any

from insar_platform.geometry.metadata_validation import MetadataValidator
from insar_platform.geometry.spatial_validation import ValidateSpatial
from insar_platform.ingestion.metadata import metadata_summary
from insar_platform.ingestion.sentinel1 import Sentinel1Ingestion


@dataclass
class CatalogDiscoveryResult:
    bursts_by_product: dict[str, list[dict[str, Any]]]
    acquisitions: dict[str, Any]
    common_coverage_percentage: float
    identity_results: dict[str, Any]
    orbit_metadata: dict[str, Any]


def catalog_discovery_pipeline(
    sentinel1_ingestion: Sentinel1Ingestion,
    logger: logging.Logger,
    aoi_wkt: str,
    config: dict,
) -> CatalogDiscoveryResult:

    # Search for Sentinel-1 bursts within the AOI
    bursts = sentinel1_ingestion.search_bursts()

    if not bursts:
        raise ValueError("No Sentinel-1 bursts found")

    logger.info("Found %d Sentinel-1 bursts", len(bursts))
    logger.debug("Sentinel-1 burst: %s", bursts[0])

    # Summarize metadata for the discovered bursts
    # Metadata return platform count (PlatformSerialIdentifier)
    # and track count (OrbitDirection, RelativeOrbitNumber, SwathIdentifier)
    metadata = metadata_summary(bursts=bursts)
    logger.info("Metadata summary: %s", metadata)

    # Group bursts by their respective SLC products
    products_by_group = sentinel1_ingestion.get_products_by_group(bursts)
    for group, products in sorted(products_by_group.items()):
        logger.info("Group: %s, Unique SLC products: %d", group, len(products))

    # Retrieve burst dates for the found bursts
    bursts_dates = sentinel1_ingestion.get_bursts_dates(bursts)
    logger.info("Sentinel-1 burst dates: %s", bursts_dates)

    # Retrieve product ID information for the found bursts
    product_id_info = sentinel1_ingestion.get_id(bursts)
    logger.info("Sentinel-1 product ID info: %s", product_id_info)

    # Retrieve deduplicated burst IDs for the found bursts
    deduplicated_id = sentinel1_ingestion.get_deduplicated_id(bursts)
    if deduplicated_id:
        logger.warning("Duplicate burst IDs: %s", deduplicated_id)

    burst = bursts[0]
    logger.info(
        "GeoFootprint: %s",
        burst["GeoFootprint"],
    )

    # Retrieve bursts grouped by their respective products
    bursts_by_product = sentinel1_ingestion.get_bursts_by_product(bursts)
    for product_id, product_bursts in bursts_by_product.items():
        logger.debug(
            "Product: %s | Burst count: %d",
            product_id,
            len(product_bursts),
        )

    # Validate spatial coverage of bursts within the AOI
    validate_spatial_instance = ValidateSpatial(
        logger=logger,
        bursts_by_product=bursts_by_product,
        aoi_wkt=aoi_wkt,
    )

    # Quantify spatial coverage of bursts within the AOI
    coverage_acquisition = validate_spatial_instance.quantify_coverage()

    # ? Script for exporting the common footprint of the AOI (currently commented out)
    # common_footprint = coverage_acquisition["common_footprint"]

    # export_processing_aoi(
    #     common_footprint=common_footprint,
    #     output_path=Path("data/aoi/alto_guadalentin_processing.geojson"),
    # )

    acquisitions = coverage_acquisition.acquisitions
    common_coverage_percentage = coverage_acquisition.common_coverage_percentage

    for product_id, acquisition_info in acquisitions.items():
        coverage_percentage = acquisition_info["coverage_percentage"]

        logger.debug(
            "Product: %s | AOI coverage: %.2f%%",
            product_id,
            coverage_percentage,
        )

    logger.info(
        "Common AOI coverage across %d acquisitions: %.2f%%",
        len(acquisitions),
        common_coverage_percentage,
    )

    validation_metadata = MetadataValidator(
        bursts_by_product=bursts_by_product,
        config=config,
        logger=logger,
    )

    # Validate burst identity across products
    identity_results = validation_metadata.validate_burst_identity()

    logger.info(
        "Reference burst IDs: %s",
        identity_results["reference_burst_ids"],
    )

    logger.info(
        "Burst-number consistency across %d acquisitions: %s",
        len(identity_results["products"]),
        identity_results["all_consistent"],
    )

    for product_id, result in identity_results["products"].items():
        if not result["is_consistent"]:
            logger.warning(
                "Product %s | Missing: %s | Unexpected: %s | Duplicates: %s",
                product_id,
                result["missing"],
                result["unexpected"],
                result["has_duplicates"],
            )

    # Validate acquisition metadata against expected values
    metadata_mismatches = validation_metadata.validate_acquisition_metadata()

    if metadata_mismatches:
        for mismatch in metadata_mismatches:
            logger.error("Acquisition metadata mismatch: %s", mismatch)

        raise ValueError("Acquisition metadata validation failed")

    logger.info("Acquisition metadata validation passed")

    # Inspect orbit metadata
    orbit_metadata = validation_metadata.inspect_orbit_metadata()

    logger.debug(
        "Orbit metadata for product %s: %s",
        orbit_metadata["product_id"],
        orbit_metadata["orbit_fields"],
    )

    logger.debug(
        "Available fields for product %s: %s",
        orbit_metadata["product_id"],
        orbit_metadata["available_fields"],
    )

    return CatalogDiscoveryResult(
        bursts_by_product=bursts_by_product,
        acquisitions=acquisitions,
        common_coverage_percentage=common_coverage_percentage,
        identity_results=identity_results,
        orbit_metadata=orbit_metadata,
    )
