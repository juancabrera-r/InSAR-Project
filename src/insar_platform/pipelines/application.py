import logging
from pathlib import Path

from insar_platform.database.services.processing_run import run_tracked_pipeline
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
        tracking_logger=logger,  # Logger used by the tracking wrapper
        sentinel1_ingestion=sentinel1_ingestion,
        logger=logger,  # Logger forwarded to catalog_discovery_pipeline
        aoi_wkt=aoi_wkt,
        config=config,
    )

    # 4. Download and prepare acquisitions
    acquisitions = run_tracked_pipeline(
        "acquisition",
        acquisition_pipeline,
        tracking_logger=logger,
        catalog=catalog,
        config=config,
        env=env,
        logger=logger,
    )

    # 5. Process geometry
    baseline = run_tracked_pipeline(
        "insar",
        insar_processing_pipeline,
        tracking_logger=logger,
        acquisitions=acquisitions,
        logger=logger,
        catalogue_burst_id=219,
    )

    # TODO: Remove, only for test
    # explore_sar_tiff = ExploreSARTiff(
    #     tiff_path=Path(
    #         "data/raw/measurement/216aff3b-f44e-4d82-9e71-f28a43fce10d/s1a-iw3-slc-vv-20250116t181040-20250116t181106-057473-0713a0-006.tiff"
    #     ),
    #     logger=logger,
    # )
    # explore_sar_tiff.run()

    logger.info(
        "Sentinel-1 processing completed | Baseline: %.3f m",
        baseline.total_m,
    )
