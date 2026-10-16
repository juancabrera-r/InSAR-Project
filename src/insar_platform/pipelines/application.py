import logging
from pathlib import Path

from insar_platform.database.services.processing_run import (
    TrackingOptions,
    run_tracked_pipeline,
)
from insar_platform.geometry.geojson_manage import GeojsonManager
from insar_platform.ingestion.sentinel1 import Sentinel1Ingestion
from insar_platform.pipelines.acquisition import acquisition_pipeline
from insar_platform.pipelines.catalog import catalog_discovery_pipeline
from insar_platform.pipelines.insar import insar_processing_pipeline


def sentinel1_app(
    config: dict,
    env: dict,
) -> None:
    logger = logging.getLogger(__name__)

    logger.info("Starting Sentinel-1 processing")

    # 1. Load AOI
    aoi_path = Path(config["AOI_GEOJSON"])

    geojson_manager = GeojsonManager(aoi_path)
    aoi_wkt = geojson_manager.polygon_to_wkt()

    # 2. Initialize Sentinel-1 ingestion
    sentinel1_ingestion = Sentinel1Ingestion(
        config=config,
        aoi_wkt=aoi_wkt,
    )

    # 3. Discover and validate catalog
    catalog = run_tracked_pipeline(
        "catalog",
        catalog_discovery_pipeline,
        logger,
        tracking_options=TrackingOptions(
            product_names_from_result=lambda result: [
                bursts[0]["ParentProductName"]
                for bursts in result.bursts_by_product.values()
                if bursts
            ],
        ),
        sentinel1_ingestion=sentinel1_ingestion,
        logger=logger,
        aoi_wkt=aoi_wkt,
        config=config,
    )

    acquisitions = run_tracked_pipeline(
        "acquisition",
        acquisition_pipeline,
        tracking_logger=logger,
        catalog=catalog,
        config=config,
        env=env,
        logger=logger,
        pass_run_id=True,
    )

    baseline = run_tracked_pipeline(
        "insar",
        insar_processing_pipeline,
        tracking_logger=logger,
        pass_run_id=True,
        acquisitions=acquisitions,
        logger=logger,
        catalogue_burst_id=config["CATALOGUE_BURST_ID"],
    )

    # Coregistration

    # Interferogram

    logger.info(
        "Sentinel-1 processing completed | Baseline: %.3f m",
        baseline.total_m,
    )
