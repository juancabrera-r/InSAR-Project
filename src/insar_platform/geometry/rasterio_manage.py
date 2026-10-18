from logging import Logger

import rasterio


class RasterioManager:
    def __init__(
        self,
        tiff_path: str,
        logger: Logger,
    ):
        self.tiff_path = tiff_path
        self.logger = logger

    def inspect_metadata_tiff(self) -> dict:
        self.logger.info("Inspecting TIFF metadata: %s", self.tiff_path)

        with rasterio.open(self.tiff_path) as src:
            metadata = {
                "driver": src.driver,
                "width": src.width,
                "height": src.height,
                "bands": src.count,
                "dtypes": list(src.dtypes),
                "crs": src.crs.to_string() if src.crs else None,
                "transform": list(src.transform)[:6],
            }

        for key, value in metadata.items():
            self.logger.info("%s: %s", key, value)

        return metadata
