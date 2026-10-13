import numpy as np
from pyproj import Transformer


def geodetic_to_ecef(
    latitude: float,
    longitude: float,
    height: float,
) -> np.ndarray:
    """Convert WGS84 geodetic coordinates to ECEF meters."""

    transformer = Transformer.from_crs(
        "EPSG:4979",
        "EPSG:4978",
        always_xy=True,
    )

    x, y, z = transformer.transform(
        longitude,
        latitude,
        height,
    )

    return np.array([x, y, z], dtype=np.float64)
