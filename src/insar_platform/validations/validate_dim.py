import logging
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np
import rasterio

LOG = logging.getLogger(__name__)


def validate_dim(
    path: Path,
    *,
    min_valid_fraction: float = 0.01,
    sample_size: int = 256,
) -> None:
    """Validate SNAP BEAM-DIMAP structure and sample raster readability."""

    path = Path(path).resolve()
    data_dir = path.with_suffix(".data")

    if not path.is_file():
        raise FileNotFoundError(f"Missing DIMAP metadata: {path}")

    if not data_dir.is_dir():
        raise FileNotFoundError(f"Missing DIMAP data directory: {data_dir}")

    if not 0.0 <= min_valid_fraction <= 1.0:
        raise ValueError("min_valid_fraction must be between 0 and 1")

    if sample_size < 1:
        raise ValueError("sample_size must be positive")

    try:
        root = ET.parse(path).getroot()
    except ET.ParseError as exc:
        raise RuntimeError(f"Invalid DIMAP XML: {path}") from exc

    def get_text(tag_name: str) -> str | None:
        for element in root.iter():
            if element.tag.split("}")[-1] == tag_name and element.text:
                return element.text.strip()

        return None

    width_text = get_text("NCOLS")
    height_text = get_text("NROWS")

    if not width_text or not height_text:
        raise RuntimeError("DIMAP raster dimensions are missing")

    width = int(width_text)
    height = int(height_text)

    if width <= 0 or height <= 0:
        raise RuntimeError(f"Invalid raster dimensions: {width} x {height}")

    band_names = [
        element.text.strip()
        for element in root.iter()
        if element.tag.split("}")[-1] == "BAND_NAME" and element.text
    ]

    if not band_names:
        raise RuntimeError("No raster bands declared in DIMAP metadata")

    # Only validate full-resolution SAR raster bands.
    # Tie-point grids have their own dimensions and must not be
    # compared against the main raster dimensions.
    raster_files = sorted(path for path in data_dir.glob("*.img") if path.is_file())

    if not raster_files:
        raise RuntimeError("No raster .img files found")

    LOG.info(
        "Validating %s | Dimensions: %d x %d | Bands: %s",
        path.name,
        width,
        height,
        band_names,
    )

    readable = 0

    for raster_file in raster_files:
        try:
            with rasterio.open(raster_file) as dataset:
                if dataset.width != width or dataset.height != height:
                    raise RuntimeError(
                        f"Dimension mismatch in {raster_file.name}: "
                        f"{dataset.width} x {dataset.height}, "
                        f"expected {width} x {height}"
                    )

                # Read a reduced-resolution sample, not the entire raster.
                data = dataset.read(
                    1,
                    out_shape=(
                        min(sample_size, height),
                        min(sample_size, width),
                    ),
                    masked=True,
                )

                valid = ~np.ma.getmaskarray(data)
                values = np.asarray(data.data)

                finite = np.isfinite(values)
                valid &= finite

                valid_fraction = float(valid.mean())

                LOG.info(
                    "Raster %s | dtype=%s | valid_fraction=%.3f",
                    raster_file.name,
                    dataset.dtypes[0],
                    valid_fraction,
                )

                if valid_fraction < min_valid_fraction:
                    LOG.warning(
                        "Low sampled valid-pixel fraction: %s (%.3f)",
                        raster_file.name,
                        valid_fraction,
                    )

                readable += 1

        except Exception as exc:
            raise RuntimeError(f"Cannot validate raster {raster_file}") from exc

    if readable == 0:
        raise RuntimeError("No readable raster bands found")

    LOG.info("DIMAP integrity validation completed: %s", path)
