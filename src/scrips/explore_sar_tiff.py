from logging import Logger
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import rasterio
from rasterio.windows import Window


class ExploreSARTiff:
    def __init__(
        self,
        tiff_path: Path,
        logger: Logger,
    ):
        self.tiff_path = tiff_path
        self.logger = logger

    def inspect_metadata(self):
        with rasterio.open(self.tiff_path) as src:
            self.logger.debug("Driver: %s", src.driver)
            self.logger.debug("Width: %s", src.width)
            self.logger.debug("Height: %s", src.height)
            self.logger.debug("Bands: %s", src.count)
            self.logger.debug("Data types: %s", src.dtypes)
            self.logger.debug("CRS: %s", src.crs)
            self.logger.debug("Transform: %s", src.transform)

    def inspect_small_amplitude_window(self):
        with rasterio.open(self.tiff_path) as src:
            # Small subset, not the entire SLC
            window = Window(
                col_off=12000,
                row_off=5500,
                width=1024,
                height=1024,
            )

            slc = src.read(1, window=window)

        zero_fraction_by_row = np.mean(slc == 0, axis=1)

        for row in (500, 540, 560, 580, 620):
            self.logger.debug(
                f"Global line {5500 + row}: "
                f"{zero_fraction_by_row[row]:.1%} zero samples"
            )

        amplitude = np.abs(slc.astype(np.complex64))

        # Logarithmic display scaling
        amplitude_db = 20 * np.log10(np.maximum(amplitude, 1e-6))

        plt.figure(figsize=(9, 7))
        plt.imshow(
            amplitude_db,
            cmap="gray",
            vmin=np.percentile(amplitude_db, 2),
            vmax=np.percentile(amplitude_db, 98),
        )
        plt.title("Sentinel-1 IW3 VV — SLC amplitude (dB)")
        plt.xlabel("Range samples")
        plt.ylabel("Azimuth lines")
        plt.colorbar(label="Amplitude (dB)")
        plt.tight_layout()
        plt.show()
