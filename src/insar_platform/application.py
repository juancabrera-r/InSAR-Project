import logging
from pathlib import Path

from ingestion.sentinel1 import Sentinel1Ingestion
from utils.geojson_manage import GeojsonManager


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

    acquisitions = sentinel1_ingestion.search_acquisitions()

    logger.info("Found %d Sentinel-1 acquisitions", len(acquisitions))
