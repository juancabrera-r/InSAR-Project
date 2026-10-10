import logging
from pathlib import Path

from ingestion.metadata import metadata_summary
from ingestion.sentinel1 import Sentinel1Ingestion
from utils.geojson_manage import GeojsonManager
from utils.spatial_validation import ValidateSpatial


# Application entry point for the InSAR platform
def sentinel1_app(
    config: dict,
    aoi_path: Path,
) -> None:
    logger = logging.getLogger(__name__)

    logger.info("Starting Sentinel-1 burst search")

    geojson_manager = GeojsonManager(aoi_path)

    aoi_wkt = geojson_manager.polygon_to_wkt()

    sentinel1_ingestion = Sentinel1Ingestion(config=config, aoi_wkt=aoi_wkt)

    bursts = sentinel1_ingestion.search_bursts()

    logger.info("Found %d Sentinel-1 bursts", len(bursts))
    # logger.debug("Sentinel-1 burst: %s", bursts[0])

    metadata_summary(bursts=bursts, logger=logger)

    products_by_group = sentinel1_ingestion.get_products_by_group(bursts)
    for group, products in sorted(products_by_group.items()):
        logger.info("Group: %s, Unique SLC products: %d", group, len(products))

    bursts_dates = sentinel1_ingestion.get_bursts_dates(bursts)
    logger.info("Sentinel-1 burst dates: %s", bursts_dates)

    product_id_info = sentinel1_ingestion.get_id(bursts)
    logger.info("Sentinel-1 product ID info: %s", product_id_info)

    deduplicated_id = sentinel1_ingestion.get_deduplicated_id(bursts)
    if deduplicated_id:
        logger.info("Duplicate burst IDs: %s", deduplicated_id)

    burst = bursts[0]
    logger.info(
        "GeoFootprint: %s",
        burst["GeoFootprint"],
    )

    bursts_by_product = sentinel1_ingestion.get_bursts_by_product(bursts)
    for product_id, product_bursts in bursts_by_product.items():
        logger.info(
            "Product: %s | Burst count: %d",
            product_id,
            len(product_bursts),
        )

    validate_spatial_instance = ValidateSpatial(
        logger=logger,
        bursts_by_product=bursts_by_product,
        aoi_wkt=aoi_wkt,
    )

    coverage_acquisition = validate_spatial_instance.quantify_coverage()

    # common_footprint = coverage_acquisition["common_footprint"]

    # export_processing_aoi(
    #     common_footprint=common_footprint,
    #     output_path=Path("data/aoi/alto_guadalentin_processing.geojson"),
    # )

    acquisitions = coverage_acquisition["acquisitions"]
    common_coverage_percentage = coverage_acquisition["common_coverage_percentage"]

    for product_id, acquisition_info in acquisitions.items():
        coverage_percentage = acquisition_info["coverage_percentage"]

        logger.info(
            "Product: %s | AOI coverage: %.2f%%",
            product_id,
            coverage_percentage,
        )

    logger.info(
        "Common AOI coverage across %d acquisitions: %.2f%%",
        len(acquisitions),
        common_coverage_percentage,
    )

    burst_identity_by_product = validate_spatial_instance.inspect_burst_identity()

    for product_id, burst_identities in burst_identity_by_product.items():
        logger.info(
            "Product: %s | Burst identities: %s",
            product_id,
            burst_identities,
        )

    identity_results = validate_spatial_instance.validate_burst_identity()

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

    logger.info("Sentinel-1 burst search completed")
