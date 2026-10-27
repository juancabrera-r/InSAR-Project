"""Main Sentinel-1 processing application."""

import logging
from pathlib import Path

from insar_platform.coregistration.inspect_coherence import inspect_coherence
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
from insar_platform.pipelines.coregistration import coregistration_pipeline
from insar_platform.pipelines.insar import insar_processing_pipeline
from insar_platform.pipelines.insar_interferogram import generate_interferogram
from insar_platform.pipelines.phase_filtering import phase_filtering_pipeline
from insar_platform.pipelines.phase_unwrapping import run_phase_unwrapping
from insar_platform.utils.cache_manager import load_cached_result, save_cached_result


def resolve_directory(
    project_root: Path,
    directory: str | Path,
) -> Path:
    """Resolve a configured path relative to the project root."""

    path = Path(directory).expanduser()

    if not path.is_absolute():
        path = project_root / path

    return path.resolve()


def load_existing_snap_product(output_dir: Path) -> Path:
    """Locate a single existing SNAP DIMAP product."""

    if not output_dir.is_dir():
        raise FileNotFoundError(f"SNAP output directory not found: {output_dir}")

    products = sorted(output_dir.glob("*.dim"))

    if not products:
        raise FileNotFoundError(f"No SNAP .dim product found in {output_dir}")

    if len(products) != 1:
        raise RuntimeError(
            f"Expected one SNAP .dim product in {output_dir}, "
            f"found {len(products)}. Explicit selection is required."
        )

    product = products[0]
    data_directory = product.with_suffix(".data")

    if not data_directory.is_dir():
        raise FileNotFoundError(
            f"SNAP product data directory not found: {data_directory}"
        )

    return product


def sentinel1_app(
    project_root: Path,
    config: dict,
    env: dict,
) -> None:
    """Execute the Sentinel-1 processing workflow."""

    logger = logging.getLogger(__name__)
    logger.info("Starting Sentinel-1 processing")

    workflow_config = config.get("CONFIG", {})

    skip_catalog = workflow_config.get("skip_catalog", False)
    skip_download = workflow_config.get("skip_download", False)
    skip_inspect_coregistration_cache = workflow_config.get(
        "skip_inspect_coregistration_cache", False
    )
    skip_baseline = workflow_config.get("skip_baseline", False)
    skip_coregistration = workflow_config.get("skip_coregistration", False)
    skip_interferogram = workflow_config.get("skip_interferogram", False)
    skip_inspect_coherence = workflow_config.get("skip_inspect_coherence", False)
    skip_phase_filtering = workflow_config.get("skip_phase_filtering", False)
    skip_phase_unwrapping = workflow_config.get("skip_phase_unwrapping", False)

    cache_directory = resolve_directory(
        project_root,
        workflow_config.get(
            "pipeline_cache_dir",
            "data/cache/pipelines",
        ),
    )

    catalog_cache = cache_directory / "catalog.pkl"
    acquisitions_cache = cache_directory / "acquisitions.pkl"
    baseline_cache = cache_directory / "baseline.pkl"
    inspect_coregistration_cache = cache_directory / "inspect_coregistration.pkl"
    coregistration_cache = cache_directory / "coregistration.pkl"
    interferogram_cache = cache_directory / "interferogram.pkl"
    coherence_cache = cache_directory / "coherence.pkl"
    phase_filtering_cache = cache_directory / "phase_filtering.pkl"
    phase_unwrapping_cache = cache_directory / "phase_unwrapping.pkl"

    _check_file_exists("catalog", skip_catalog, catalog_cache)
    _check_file_exists("acquisitions", skip_download, acquisitions_cache)
    _check_file_exists("baseline", skip_baseline, baseline_cache)
    _check_file_exists(
        "inspect_coregistration",
        skip_inspect_coregistration_cache,
        inspect_coregistration_cache,
    )
    _check_file_exists("coregistration", skip_coregistration, coregistration_cache)
    _check_file_exists("interferogram", skip_interferogram, interferogram_cache)
    _check_file_exists("phase_filtering", skip_phase_filtering, phase_filtering_cache)
    _check_file_exists(
        "phase_unwrapping", skip_phase_unwrapping, phase_unwrapping_cache
    )

    # 1. Prepare area of interest.
    aoi_path = resolve_directory(
        project_root,
        config["AOI_GEOJSON"],
    )

    aoi_wkt = GeojsonManager(aoi_path).polygon_to_wkt()

    # 2. Discover or restore catalog.
    if skip_catalog:
        logger.info("Restoring cached catalog: %s", catalog_cache)
        catalog = load_cached_result(catalog_cache)

    else:
        sentinel1_ingestion = Sentinel1Ingestion(
            config=config,
            aoi_wkt=aoi_wkt,
        )

        tracking_options = TrackingOptions(
            product_names_from_result=lambda result: {
                bursts[0]["ParentProductName"]
                for bursts in result.bursts_by_product.values()
                if bursts
            },
        )

        catalog = run_tracked_pipeline(
            "catalog",
            catalog_discovery_pipeline,
            logger,
            tracking_options=tracking_options,
            sentinel1_ingestion=sentinel1_ingestion,
            logger=logger,
            aoi_wkt=aoi_wkt,
            config=config,
        )

        save_cached_result(catalog_cache, catalog)

    # 3. Download or restore acquisitions.
    if skip_download:
        logger.info(
            "Restoring cached acquisitions: %s",
            acquisitions_cache,
        )
        acquisitions = load_cached_result(acquisitions_cache)

    else:
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

        save_cached_result(acquisitions_cache, acquisitions)

    if acquisitions is None:
        raise RuntimeError(
            "Acquisition metadata is unavailable. "
            "Cannot continue Sentinel-1 processing."
        )

    # 4. Calculate InSAR baseline geometry.
    orbit_directory = resolve_directory(
        project_root,
        config["ORBIT_DATA_RAW"],
    )

    if not orbit_directory.is_dir():
        raise FileNotFoundError(f"Orbit directory not found: {orbit_directory}")

    if skip_baseline:
        logger.info(
            "Restoring cached baseline: %s",
            baseline_cache,
        )
        baseline = load_cached_result(baseline_cache)

    else:
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

        save_cached_result(baseline_cache, baseline)

    # 5. Inspect SAFE dependencies.
    raw_directory = resolve_directory(
        project_root,
        Path(config["ANNOTATION_DATA_RAW"]).parent,
    )

    if skip_inspect_coregistration_cache:
        logger.info(
            "Restoring cached inspect coregistration: %s",
            inspect_coregistration_cache,
        )
        inspect_coregistration = load_cached_result(inspect_coregistration_cache)
    else:
        inspect_coregistration = run_tracked_pipeline(
            "inspect_coregistration",
            inspect_coregistration_pipeline,
            tracking_logger=logger,
            acquisitions=acquisitions,
            raw_directory=raw_directory,
            logger=logger,
        )

        save_cached_result(inspect_coregistration_cache, inspect_coregistration)

    if inspect_coregistration.total_missing:
        raise RuntimeError(
            f"Missing SAFE dependencies: {inspect_coregistration.total_missing}"
        )

    # 6. Coregistration.
    coregistration_output = resolve_directory(
        project_root,
        config["COREGISTRATION_OUTPUT"],
    )

    if skip_coregistration:
        logger.info("Reusing existing SNAP coregistration product")

        coregistered_dim = load_cached_result(coregistration_cache)

    else:
        coregistered_dim = run_tracked_pipeline(
            "coregistration",
            coregistration_pipeline,
            tracking_logger=logger,
            acquisitions=acquisitions,
            raw_directory=raw_directory,
            output_dir=coregistration_output,
            logger=logger,
            safe_inspection=inspect_coregistration,
            first_burst=int(config["SNAP_FIRST_BURST"]),
            last_burst=int(config["SNAP_LAST_BURST"]),
            gpt=config["SNAP_GPT"],
            dem_name=config.get(
                "SNAP_DEM_NAME",
                "Copernicus 30m Global DEM",
            ),
            cache=config.get("SNAP_CACHE", "4G"),
            threads=int(config.get("SNAP_THREADS", 2)),
            run_esd=config.get("SNAP_RUN_ESD", True),
            subswath=config["SAR_PARAMETERS"]["SWATH"],
            polarization=config["COPERNICUS_PRODUCT"]["POLARIZATION"],
        )

        save_cached_result(coregistration_cache, coregistered_dim)

    # 7. Generate interferogram and coherence.
    if skip_interferogram:
        logger.info("Reusing existing SNAP interferogram product")

        interferogram_dim = load_cached_result(interferogram_cache)
    else:
        interferogram_dim = run_tracked_pipeline(
            "interferogram",
            generate_interferogram,
            tracking_logger=logger,
            input_dim=coregistered_dim,
            output_dir=resolve_directory(
                project_root,
                config["INTERFEROGRAM_OUTPUT"],
            ),
            gpt=config["SNAP_GPT"],
            cache=config.get("SNAP_CACHE", "4G"),
            threads=int(config.get("SNAP_THREADS", 2)),
            coherence_window_meters=int(config.get("COHERENCE_WINDOW_METERS", 30)),
            subtract_flat_earth=config.get(
                "INTERFEROGRAM_SUBTRACT_FLAT_EARTH",
                True,
            ),
            subtract_topographic_phase=config.get(
                "INTERFEROGRAM_SUBTRACT_TOPOGRAPHIC_PHASE",
                False,
            ),
            dem_name=config.get(
                "SNAP_DEM_NAME",
                "Copernicus 30m Global DEM",
            ),
            deburst=config.get("INTERFEROGRAM_DEBURST", True),
        )

        save_cached_result(interferogram_cache, interferogram_dim)

    # 8. Inspect coherence.
    if skip_inspect_coherence:
        logger.info("Reusing existing coherence report")
        coherence_report = load_cached_result(coherence_cache)
    else:
        coherence_report = run_tracked_pipeline(
            "inspect_coherence",
            inspect_coherence,
            tracking_logger=logger,
            input_dim=interferogram_dim,
            output_dir=resolve_directory(
                project_root,
                config["COHERENCE_REPORT_OUTPUT"],
            ),
            histogram_bins=int(config.get("COHERENCE_HISTOGRAM_BINS", 10000)),
        )

        save_cached_result(coherence_cache, coherence_report)

    # 11. Apply Goldstein phase filtering.
    if skip_phase_filtering:
        logger.info("Reusing existing filtered interferogram")
        filtered_dim = load_cached_result(phase_filtering_cache)
    else:
        filtered_dim = run_tracked_pipeline(
            "phase_filtering",
            phase_filtering_pipeline,
            tracking_logger=logger,
            interferogram_dim=interferogram_dim,
            output_dir=resolve_directory(
                project_root,
                config["PHASE_FILTERING_OUTPUT"],
            ),
            logger=logger,
            gpt=config["SNAP_GPT"],
            cache=config.get("SNAP_CACHE", "4G"),
            threads=int(config.get("SNAP_THREADS", 2)),
            alpha=float(config.get("GOLDSTEIN_ALPHA", 1.0)),
            fft_size=int(config.get("GOLDSTEIN_FFT_SIZE", 64)),
            window_size=int(config.get("GOLDSTEIN_WINDOW_SIZE", 3)),
            use_coherence_mask=config.get(
                "GOLDSTEIN_USE_COHERENCE_MASK",
                False,
            ),
        )

        save_cached_result(phase_filtering_cache, filtered_dim)

    # 12. Run phase unwrapping.
    if skip_phase_unwrapping:
        logger.info("Reusing existing phase unwrapping result")
        phase_unwrapping_result = load_cached_result(phase_unwrapping_cache)
    else:
        phase_unwrapping_result = run_tracked_pipeline(
            "phase_unwrapping",
            run_phase_unwrapping,
            tracking_logger=logger,
            filtered_product=(
                Path(config["PHASE_FILTERING_OUTPUT"]) / "filtered_interferogram.dim"
            ),
            output_directory=config["PHASE_UNWRAPPING_OUTPUT"],
            gpt=config["SNAP_GPT"],
            snaphu_executable=config["SNAPHU_EXECUTABLE"],
            stat_cost_mode=config.get("SNAPHU_STAT_COST_MODE", "DEFO"),
            init_method=config.get("SNAPHU_INIT_METHOD", "MST"),
            tile_rows=config.get("SNAPHU_TILE_ROWS", 4),
            tile_cols=config.get("SNAPHU_TILE_COLS", 8),
            row_overlap=config.get("SNAPHU_ROW_OVERLAP", 200),
            col_overlap=config.get("SNAPHU_COL_OVERLAP", 200),
            processors=config.get("SNAPHU_PROCESSORS", 4),
            tile_cost_threshold=config.get("SNAPHU_TILE_COST_THRESHOLD", 500),
            keep_wrapped=config.get("SNAPHU_KEEP_WRAPPED", True),
        )

        save_cached_result(phase_unwrapping_cache, phase_unwrapping_result)

    logger.info(
        "Sentinel-1 processing completed | "
        "Baseline: %.3f m | "
        "Missing SAFE dependencies: %d | "
        "Mean coherence: %.4f | "
        "Median coherence: %.4f | "
        "Pixels above 0.6: %.2f%%",
        baseline.total_m,
        inspect_coregistration.total_missing,
        coherence_report.mean,
        coherence_report.median,
        coherence_report.percent_above_06,
    )


def _check_file_exists(
    name: str,
    skip: bool,
    file_path: Path,
) -> None:
    """Validate that a cache file exists when a pipeline stage is skipped."""
    if skip and not file_path.is_file():
        raise FileNotFoundError(
            f"Cached file for {name} not found: {file_path}. "
            "Run the pipeline without skipping this stage."
        )
