import numpy as np
from pyproj import Transformer


def test_geodetic_ecef_roundtrip():
    forward = Transformer.from_crs("EPSG:4979", "EPSG:4978", always_xy=True)
    inverse = Transformer.from_crs("EPSG:4978", "EPSG:4979", always_xy=True)

    lon, lat, h = -2.387762810598634, 36.96933193985169, 720.954042930156

    x, y, z = forward.transform(lon, lat, h)
    lon2, lat2, h2 = inverse.transform(x, y, z)

    np.testing.assert_allclose(
        [lon2, lat2, h2],
        [lon, lat, h],
        rtol=0,
        atol=1e-7,
    )
