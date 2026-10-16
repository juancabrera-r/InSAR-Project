import logging
import uuid
from pathlib import Path

from insar_platform.database.models.sar_product_file import SARFileType
from insar_platform.database.services.insar_baseline import (
    InSARBaselineRecord,
    register_insar_baseline,
)
from insar_platform.database.services.processing_run import (
    TrackingOptions,
    run_tracked_pipeline,
)
from insar_platform.database.services.sar_product_file import (
    SARProductFileRecord,
    register_sar_product_files,
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

    # 4. Download and prepare acquisitions
    def save_acquisition_files(
        run_id: uuid.UUID,
        acquisitions,
    ) -> None:
        records = []

        for acquisition in acquisitions:
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
            records=records,
            download_run_id=None,
        )

        logger.info(
            "Registered %d new SAR product files | Run ID: %s",
            inserted_count,
            run_id,
        )

    acquisitions = run_tracked_pipeline(
        "acquisition",
        acquisition_pipeline,
        tracking_logger=logger,
        tracking_options=TrackingOptions(
            result_handler=save_acquisition_files,
        ),
        catalog=catalog,
        config=config,
        env=env,
        logger=logger,
    )

    # 5. Process geometry
    catalogue_burst_id = 219

    def save_baseline(
        run_id: uuid.UUID,
        baseline,
    ) -> None:
        record = InSARBaselineRecord(
            reference_product_name=acquisitions[0].product_name,
            secondary_product_name=acquisitions[1].product_name,
            catalogue_burst_id=catalogue_burst_id,
            total_m=baseline.total_m,
            perpendicular_m=baseline.perpendicular_m,
            parallel_m=baseline.parallel_m,
        )

        baseline_id = register_insar_baseline(
            run_id=run_id,
            record=record,
        )

        logger.info(
            "Registered InSAR baseline: %s | Run ID: %s",
            baseline_id,
            run_id,
        )

    baseline = run_tracked_pipeline(
        "insar",
        insar_processing_pipeline,
        tracking_logger=logger,
        tracking_options=TrackingOptions(
            result_handler=save_baseline,
        ),
        acquisitions=acquisitions,
        logger=logger,
        catalogue_burst_id=catalogue_burst_id,
    )

    logger.info(
        "Sentinel-1 processing completed | Baseline: %.3f m",
        baseline.total_m,
    )
