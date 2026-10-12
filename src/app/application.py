import logging
from datetime import UTC, datetime
from pathlib import Path

from app.acquisition_geometry import zero_doppler
from ingestion.annotation import extract_annotation_bursts
from ingestion.cdse import CDSEIngestion
from ingestion.metadata import metadata_summary, select_earliest_acquisitions
from ingestion.sentinel1 import Sentinel1Ingestion
from utils.calculate_orbit import CalculateOrbit
from utils.coordinate_transform import geodetic_to_ecef
from utils.geojson_manage import GeojsonManager
from utils.metadata_validation import MetadataValidator
from utils.spatial_validation import ValidateSpatial


# Application entry point for the InSAR platform
def sentinel1_app(
    config: dict,
    env: dict,
) -> None:
    logger = logging.getLogger(__name__)

    logger.info("Starting Sentinel-1 burst search")

    aoi_path = Path(config["AOI_GEOJSON"])

    geojson_manager = GeojsonManager(aoi_path)

    aoi_wkt = geojson_manager.polygon_to_wkt()

    sentinel1_ingestion = Sentinel1Ingestion(config=config, aoi_wkt=aoi_wkt)

    # Search for Sentinel-1 bursts within the AOI
    bursts = sentinel1_ingestion.search_bursts()

    if not bursts:
        raise ValueError("No Sentinel-1 bursts found")

    logger.info("Found %d Sentinel-1 bursts", len(bursts))
    logger.debug("Sentinel-1 burst: %s", bursts[0])

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
        logger.info("Duplicate burst IDs: %s", deduplicated_id)

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

    acquisitions = coverage_acquisition["acquisitions"]
    common_coverage_percentage = coverage_acquisition["common_coverage_percentage"]

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

    # Select the two earliest acquisitions from each product
    selected_pair = select_earliest_acquisitions(
        bursts_by_product,
        count=2,
    )

    # Temporary: Log all bursts for the selected acquisitions
    for acquisition in selected_pair:
        product_id = acquisition["product_id"]

        product_bursts = sorted(
            bursts_by_product[product_id],
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
    cdse_ingestion = CDSEIngestion(
        config=config,
        selected_pair=selected_pair,
        env=env,
        logger=logger,
    )

    calculate_orbit = CalculateOrbit(logger)

    satellite_positions = {}
    reference_ground_position = None

    for acquisition in selected_pair:
        product_id = acquisition["product_id"]

        logger.info(
            "Processing acquisition: %s | Product ID: %s",
            acquisition["product_name"],
            product_id,
        )

        # 1. Retrieve metadata for this acquisition
        product_metadata = cdse_ingestion.get_product_metadata(product_id=product_id)

        logger.info(
            "Product: %s | Size: %.2f GiB | Online: %s",
            product_metadata["Name"],
            product_metadata["ContentLength"] / (1024**3),
            product_metadata["Online"],
        )

        # 2. Retrieve this product's nodes
        nodes = cdse_ingestion.get_product_nodes(product_id=product_id)

        children_node = cdse_ingestion.get_children_node(product_nodes=nodes)

        annotation_files = cdse_ingestion.get_children_node_annotation(
            children_node=children_node
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
        xml_file_path = Path(config["ANNOTATION_DATA_RAW"]) / annotation_node["Name"]

        cdse_ingestion.download_annotation_file(
            annotation_node=annotation_node,
            destination_path=xml_file_path,
        )

        annotation_bursts = extract_annotation_bursts(xml_file_path=xml_file_path)

        burst_mapping = validation_metadata.map_catalogue_bursts_to_annotation(
            catalogue_bursts=bursts_by_product[product_id],
            annotation_bursts=annotation_bursts,
        )

        logger.info(
            "Product %s | Burst mapping: %s",
            product_id,
            burst_mapping,
        )

        # 5. Extract orbit vectors
        orbit_vectors = calculate_orbit.extract_orbit_xml_file(
            xml_file_path=xml_file_path
        )

        logger.info(
            "Product %s | Extracted %d orbit vectors",
            product_id,
            len(orbit_vectors),
        )

        # 6. Use the current acquisition timestamp
        raw_time = acquisition["acquisition_time"]

        if isinstance(raw_time, datetime):
            acquisition_time = raw_time
        else:
            acquisition_time = datetime.fromisoformat(raw_time)

        if acquisition_time.tzinfo is None:
            raise ValueError(f"Acquisition timestamp lacks timezone: {raw_time}")

        acquisition_time = acquisition_time.astimezone(UTC)

        # 7. Validate orbit coverage
        orbit_start = orbit_vectors[0]["timestamp"]
        orbit_end = orbit_vectors[-1]["timestamp"]

        logger.info("Acquisition time: %s", acquisition_time)
        logger.info("Annotation file: %s", xml_file_path.name)
        logger.info(
            "Orbit coverage: %s to %s",
            orbit_start,
            orbit_end,
        )

        if not orbit_start <= acquisition_time <= orbit_end:
            raise ValueError(
                f"Acquisition {acquisition_time} is outside orbit "
                f"coverage [{orbit_start}, {orbit_end}]"
            )

        # 8. Find bracketing vectors
        first, second = calculate_orbit.find_bracketing_vectors(
            orbit_vectors=orbit_vectors,
            acquisition_time=acquisition_time,
        )

        # 9. Interpolate satellite position
        catalogue_position, catalogue_velocity = calculate_orbit.interpolate_state(
            first=first,
            second=second,
            acquisition_time=acquisition_time,
        )

        logger.info(
            "Product %s | Interpolated ECEF position (m): %s",
            product_id,
            catalogue_position,
        )

        # 10. Extract geolocation points from the annotation XML
        geolocation_points = calculate_orbit.extract_geolocation_grid(xml_file_path)

        logger.info(
            "Product %s | Geolocation points: %d",
            product_id,
            len(geolocation_points),
        )

        logger.debug(
            "First geolocation point: %s",
            geolocation_points[0],
        )

        # 11. Convert geolocation points from geodetic to ECEF coordinates

        if reference_ground_position is None:
            point = calculate_orbit.select_central_geolocation_point(geolocation_points)

            logger.debug(
                "Reference geolocation point: line=%d, pixel=%d, "
                "latitude=%.8f, longitude=%.8f, height=%.3f",
                point["line"],
                point["pixel"],
                point["latitude"],
                point["longitude"],
                point["height"],
            )

            reference_ground_position = geodetic_to_ecef(
                latitude=point["latitude"],
                longitude=point["longitude"],
                height=point["height"],
            )

            logger.info(
                "Reference ground target ECEF (m): %s",
                reference_ground_position,
            )

        # 12. Zero Doppler residual for the reference ground position
        result = zero_doppler(
            logger=logger,
            product_id=product_id,
            calculate_orbit=calculate_orbit,
            catalogue_position=catalogue_position,
            catalogue_velocity=catalogue_velocity,
            reference_ground_position=reference_ground_position,
            orbit_vectors=orbit_vectors,
            satellite_positions=satellite_positions,
        )

        satellite_positions[product_id] = result.satellite_position

    logger.info(
        "Orbit interpolation completed for %d acquisitions",
        len(satellite_positions),
    )

    logger.info("Sentinel-1 burst search completed")
