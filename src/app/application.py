import logging
from pathlib import Path

from ingestion.metadata import metadata_summary
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

    bursts = sentinel1_ingestion.search_bursts()

    logger.info("Found %d Sentinel-1 bursts", len(bursts))
    # logger.debug("Sentinel-1 burst: %s", bursts[0])

    metadata_summary(bursts=bursts, logger=logger)

    sentinel1_ingestion.get_products_by_group(bursts)
    sentinel1_ingestion.get_bursts_dates(bursts)
    sentinel1_ingestion.get_id(bursts)
    sentinel1_ingestion.get_deduplicated_id(bursts)

    logger.info("Sentinel-1 burst search completed")
