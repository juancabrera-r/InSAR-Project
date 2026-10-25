"""Main Sentinel-1 processing application."""

import logging
from pathlib import Path

from insar_platform.coregistration.inspect_coregistration import (
    inspect_coregistration_pipeline,
)
from insar_platform.database.services.processing_run import (
    TrackingOptions,
    run_tracked_pipeline,
)
from insar_platform.geometry.geojson_manage import GeojsonManager
from insar_platform.ingestion.sentinel1 import Sentinel1Ingestion
from insar_platform.pipelines.acquisition import acquisition_pipeline
from insar_platform.pipelines.catalog import catalog_discovery_pipeline
from insar_platform.pipelines.insar import insar_processing_pipeline


def resolve_directory(
    project_root: Path,
    directory: str | Path,
) -> Path:
    """Resolve a configured directory relative to the project root."""

    path = Path(directory).expanduser()

    if not path.is_absolute():
        path = project_root / path

    return path.resolve()


def sentinel1_app(
    project_root: Path,
    config: dict,
    env: dict,
) -> None:
    """Execute the Sentinel-1 processing workflow."""

    logger = logging.getLogger(__name__)
    logger.info("Starting Sentinel-1 processing")

    # 1. Prepare area of interest.
    aoi_path = resolve_directory(
        project_root,
        config["AOI_GEOJSON"],
    )

    aoi_wkt = GeojsonManager(aoi_path).polygon_to_wkt()

    # 2. Initialize Sentinel-1 catalogue ingestion.
    sentinel1_ingestion = Sentinel1Ingestion(
        config=config,
        aoi_wkt=aoi_wkt,
    )

    # 3. Discover and validate catalogue acquisitions.
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

    # 4. Download and validate selected acquisitions.
    acquisitions = run_tracked_pipeline(
        "acquisition",
        acquisition_pipeline,
        tracking_logger=logger,
        pass_run_id=True,
        catalog=catalog,
        config=config,
        env=env,
        logger=logger,
    )

    # 5. Calculate InSAR baseline geometry.
    orbit_directory = resolve_directory(
        project_root,
        config["ORBIT_DATA_RAW"],
    )

    if not orbit_directory.is_dir():
        raise FileNotFoundError(f"Orbit directory not found: {orbit_directory}")

    baseline = run_tracked_pipeline(
        "insar",
        insar_processing_pipeline,
        tracking_logger=logger,
        pass_run_id=True,
        acquisitions=acquisitions,
        logger=logger,
        catalogue_burst_id=config["CATALOGUE_BURST_ID"],
        max_perpendicular_baseline_m=config.get("MAX_PERPENDICULAR_BASELINE_M"),
        max_normalized_residual=config.get("MAX_NORMALIZED_RESIDUAL"),
        satellite_names=config.get("SATELLITE_NAMES"),
        orbit_directory=orbit_directory,
    )

    # 6. Inspect SAFE dependencies for SNAP coregistration.
    raw_directory = resolve_directory(
        project_root,
        Path(config["ANNOTATION_DATA_RAW"]).parent,
    )

    inspect_coregistration = run_tracked_pipeline(
        "inspect_coregistration",
        inspect_coregistration_pipeline,
        tracking_logger=logger,
        acquisitions=acquisitions,
        raw_directory=raw_directory,
        logger=logger,
    )

    # 7. Interferogram generation (not implemented yet).

    logger.info(
        "Sentinel-1 processing completed | "
        "Baseline: %.3f m | "
        "Missing SAFE dependencies: %d",
        baseline.total_m,
        inspect_coregistration.total_missing,
    )
